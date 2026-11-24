"""Локальный, явно разрешённый обмен учебным вопросом через API.

GitHub является транспортом данных, а не источником исполняемых команд.
Первый этап: одна заранее утверждённая реплика, без автоматической
публикации непроверенного ответа и без доступа к административным маршрутам.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from .diagnostics import event
from .secret_store import load_secret

MAX_BYTES = 1024 * 1024
MAX_ANSWER = 12000


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_call(opener, url, token, payload=None, *, method="GET", timeout=20):
    headers = {"Accept": "application/json", "User-Agent": "AI-mentor",
               "Authorization": "Bearer " + token}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Превышен размер ответа")
    return json.loads(raw)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atomic(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


class MentorBridge:
    def __init__(self, brain_dir: Path, port: int):
        self.root = Path(brain_dir) / "ai-mentor"
        self.port = int(port)
        self.stop_event = threading.Event()
        self.thread = None
        self.github = request.build_opener(_NoRedirect())
        self.local = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def start(self):
        # Не создаём сеть и не читаем секреты на обычных установках.
        if not (self.root / "config.json").is_file():
            return
        self.thread = threading.Thread(target=self._run, name="leshy-ai-mentor", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)

    def _run(self):
        if self.stop_event.wait(15):
            return
        while not self.stop_event.is_set():
            try:
                _atomic(self.root / "status.json", {"status": "checking", "timestamp": _now()})
                status = self._step()
                _atomic(self.root / "status.json", {"status": status, "timestamp": _now()})
                if status in {"published", "disabled"}:
                    return
            except Exception as exc:
                # Никаких тел HTTP-ответов, запросов, токенов или приватных путей.
                event("mentor_bridge_error", error_type=type(exc).__name__)
                try:
                    _atomic(self.root / "status.json", {"status": "error", "timestamp": _now()})
                except OSError:
                    pass
            if self.stop_event.wait(3600):
                return

    def _step(self):
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        if config.get("enabled") is not True or self.stop_event.is_set():
            return "disabled"
        repo = config.get("repository")
        rid = config.get("request_id")
        question_hash = config.get("question_sha256")
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Неверный репозиторий")
        if not isinstance(rid, str) or not re.fullmatch(r"mentor-[a-z0-9-]{1,80}", rid):
            raise ValueError("Неверный идентификатор")
        if not isinstance(question_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", question_hash):
            raise ValueError("Не утверждён вопрос")
        github_token = load_secret(self.root / "github_token.dat")
        api_token = os.getenv("LESHY_API_TOKEN", "").strip()
        if not github_token or not api_token:
            event("mentor_bridge_not_configured")
            return "disabled"
        base = "https://api.github.com/repos/" + repo

        def gh(path="", payload=None, method="GET"):
            return _json_call(self.github, base + path, github_token, payload, method=method)

        def local(path, payload=None):
            return _json_call(self.local, "http://127.0.0.1:" + str(self.port) + path,
                              api_token, payload, method="POST" if payload is not None else "GET",
                              timeout=300 if payload is not None else 15)

        if gh().get("private") is not True:
            raise PermissionError("Обмен только с приватным репозиторием")
        state = self.root / (rid + ".json")
        target = "/contents/responses/" + rid + ".json"
        try:
            remote = gh(target + "?ref=main")
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
            remote = None
        if remote is not None:
            if state.is_file():
                saved = json.loads(state.read_text(encoding="utf-8"))
                received = json.loads(base64.b64decode(remote["content"]))
                if saved.get("status") == "answered" and received == saved:
                    answer = saved.get("answer")
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError("Нет подтверждённого текста ответа")
                    self._mark_published(rid, answer)
                    event("mentor_bridge_published", request_id=rid)
                    return "published"
            # Не генерируем заново и не перезаписываем чужой результат.
            raise RuntimeError("Ответ уже существует, локальное подтверждение отсутствует")
        if state.is_file():
            saved = json.loads(state.read_text(encoding="utf-8"))
            if saved.get("status") != "answered" or saved.get("question_sha256") != question_hash or saved.get("repository") != repo:
                # Тайм-аут/сбой после POST не означает, что модель не ответила.
                event("mentor_bridge_uncertain", request_id=rid)
                return "disabled"
        else:
            item = gh("/contents/requests/" + rid + ".json?ref=main")
            incoming = json.loads(base64.b64decode(item["content"]))
            question = incoming.get("question")
            if incoming.get("source") != "AI-mentor" or incoming.get("request_id") != rid:
                raise ValueError("Неверный источник запроса")
            if not isinstance(question, str) or len(question) > 4000 or _digest(question) != question_hash:
                raise PermissionError("Вопрос не совпадает с локальным разрешением")
            if github_token in question or api_token in question:
                raise PermissionError("Вопрос содержит секрет")
            _atomic(self.root / (rid + ".question.json"), {
                "request_id": rid, "question": question, "timestamp": _now(),
            })
            status = local("/v1/status")
            if status.get("backend") not in {"native", "ollama", "inkling-original", "inferssd"} or not status.get("model_ready"):
                return "waiting"
            activity = status.get("deployment_activity")
            if not isinstance(activity, dict) or activity.get("idle") is not True:
                return "waiting"
            if self.stop_event.is_set():
                return "disabled"
            with state.open("x", encoding="utf-8") as handle:
                json.dump({"request_id": rid, "status": "sent", "timestamp": _now()}, handle)
            reply = local("/v1/chat", {"message": question, "owner_id": "mentor:AI-mentor",
                                      "session_id": "ai-mentor-" + rid, "request_id": rid})
            answer = reply.get("answer")
            if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER:
                raise RuntimeError("Нет допустимого ответа API")
            if github_token in answer or api_token in answer:
                raise PermissionError("Ответ содержит секрет")
            saved = {"request_id": rid, "source": "leshy_api", "status": "answered",
                     "timestamp": _now(), "revision": status.get("version") or "not_confirmed",
                     "model": reply.get("model") if isinstance(reply.get("model"), str) else "not_confirmed",
                     "question_sha256": question_hash, "repository": repo, "answer": answer}
            _atomic(state, saved)
            event("mentor_bridge_answer_saved", request_id=rid)
        # Явное локальное разрешение именно на этот текст после проверки.
        # Ни GitHub, ни содержимое вопроса не могут включить публикацию.
        answer = saved.get("answer")
        if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER:
            raise ValueError("Некорректный локальный ответ")
        if saved.get("request_id") != rid or saved.get("source") != "leshy_api":
            raise ValueError("Некорректный локальный результат")
        if config.get("approved_answer_sha256") != _digest(answer):
            return "awaiting_review"
        if github_token in answer or api_token in answer or self.stop_event.is_set():
            raise PermissionError("Публикация запрещена")
        raw = (json.dumps(saved, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        gh(target, {"message": "Ответ локального API на " + rid, "branch": "main",
                    "content": base64.b64encode(raw).decode("ascii")}, "PUT")
        confirmed = gh(target + "?ref=main")
        if base64.b64decode(confirmed["content"]) != raw:
            raise RuntimeError("Не подтверждена публикация")
        self._mark_published(rid, answer)
        event("mentor_bridge_published", request_id=rid)
        return "published"

    def _mark_published(self, rid, answer):
        path = self.root / (rid + ".published.json")
        if path.is_file():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                if old.get("request_id") == rid and old.get("answer_sha256") == _digest(answer):
                    return
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        _atomic(path, {"request_id": rid, "answer_sha256": _digest(answer), "timestamp": _now()})
