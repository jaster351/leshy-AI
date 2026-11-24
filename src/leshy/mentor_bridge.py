"""Hourly, locally approved AI-mentor exchange. No ordinary chat or remote commands."""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib import error, request

from .diagnostics import event
from .mentor_batch import BatchRunner, atomic, digest, encode, now, read_obj, INTERVAL
from .secret_store import load_secret
from .version import VERSION

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
        if self.root.is_symlink() or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="leshy-ai-mentor", daemon=True)
        try:
            self.thread.start()
        except Exception as exc:
            self.thread = None
            event("mentor_thread_start_failed", error_type=type(exc).__name__)

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

    def _publish_runtime_version(self, config):
        """Минимальная телеметрия запущенного процесса, независимо от учебного пакета."""
        repo = config.get("repository")
        if (config.get("enabled") is not True or not isinstance(repo, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
                or self.stop_event.is_set()):
            return
        token = load_secret(self.root / "github_token.dat")
        if not token or self._gh(repo, token).get("private") is not True:
            return

        def allowed():
            current = read_obj(self.root / "config.json")
            return (not self.stop_event.is_set()
                    and current.get("enabled") is True
                    and current.get("repository") == repo
                    and load_secret(self.root / "github_token.dat") == token)

        if not allowed():
            return
        identity_path = self.root / "runtime-identity.json"
        if identity_path.exists():
            instance_id = read_obj(identity_path).get("instance_id")
        else:
            instance_id = uuid.uuid4().hex
            atomic(identity_path, {"instance_id": instance_id})
        if not isinstance(instance_id, str) or not re.fullmatch(r"[0-9a-f]{32}", instance_id):
            raise ValueError("Некорректный идентификатор экземпляра")
        record = {"schema": "ai-mentor.runtime-version.v1", "source": "leshy_runtime",
                  "instance_id": instance_id, "version": VERSION,
                  "status": "running", "timestamp": now()}
        raw = encode(record)
        target = "/contents/runtime/" + instance_id + ".json"
        payload = {"message": "Телеметрия запущенной версии Лешего",
                   "branch": "main", "content": base64.b64encode(raw).decode("ascii")}
        try:
            existing = self._gh(repo, token, target + "?ref=main")
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
        else:
            sha = existing.get("sha")
            if (existing.get("type") != "file" or not isinstance(sha, str)
                    or not re.fullmatch(r"[0-9a-f]{40}", sha)):
                raise ValueError("Некорректный удалённый файл телеметрии")
            payload["sha"] = sha
        if not allowed():
            return
        self._gh(repo, token, target, payload, "PUT")
        if not allowed():
            return
        confirmed = self._gh(repo, token, target + "?ref=main")
        if (confirmed.get("type") != "file" or confirmed.get("encoding") != "base64"
                or not isinstance(confirmed.get("content"), str)
                or base64.b64decode("".join(confirmed["content"].split()), validate=True) != raw):
            raise ValueError("Публикация версии не подтверждена чтением")
        atomic(self.root / "runtime-version-published.json",
               dict(record, sha256=digest(raw)))

    def _run(self):
        if self.stop_event.wait(15):
            return
        runner = BatchRunner(self.root, self.app, self.stop_event, self._gh)
        last_waiting_heartbeat = None
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if self.root.is_symlink() or not (self.root / "config.json").is_file():
                    if self.stop_event.wait(15):
                        return
                    continue
                config = runner.config()
                if config.get("enabled") is not True:
                    atomic(self.root / "status.json", {"status": "disabled", "timestamp": now()})
                    if self.stop_event.wait(15):
                        return
                    continue
                # Не расходуем часовой учебный интервал, пока обновлятор
                # не доставил пакет. Проверка только локальных метаданных.
                if os.name == "nt" and config.get("repository") == "jaster351/AI-mentor":
                    batch_id = config.get("batch_id")
                    expected = config.get("batch_sha256")
                    ready = (isinstance(batch_id, str)
                             and re.fullmatch(r"mentor-batch-[a-z0-9-]{1,55}", batch_id)
                             and isinstance(expected, str)
                             and re.fullmatch(r"[0-9a-f]{64}", expected))
                    if ready:
                        delivery = self.root / (batch_id + ".delivery.json")
                        ready = delivery.is_file() and not delivery.is_symlink()
                    if not ready:
                        # Телеметрия версии остаётся независимой от пакета.
                        current_time = time.monotonic()
                        if last_waiting_heartbeat is None or current_time - last_waiting_heartbeat >= INTERVAL:
                            last_waiting_heartbeat = current_time
                            try:
                                self._publish_runtime_version(config)
                            except Exception as exc:
                                event("mentor_runtime_version_failed", error_type=type(exc).__name__)
                        atomic(self.root / "status.json",
                               {"status": "waiting_delivery", "timestamp": now()})
                        if self.stop_event.wait(15):
                            return
                        continue
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
                        continue
                atomic(clock, {"started_epoch": time.time()})
                started = time.monotonic()
                atomic(self.root / "status.json", {"status": "checking", "timestamp": now()})
                try:
                    self._publish_runtime_version(config)
                except Exception as exc:
                    event("mentor_runtime_version_failed", error_type=type(exc).__name__)
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
