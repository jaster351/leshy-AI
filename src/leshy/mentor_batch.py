"""Последовательная учебная диагностика без обычного чата и личной памяти."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error

from .inference_scheduler import get_inference_scheduler, PRIORITY_BACKGROUND
from .llm import EchoLLM, finalize_model_output
from .diagnostics import event
from .secret_store import load_secret
from .mentor_performance import CyclePerformance, capture_mentor_inference, cpu_clock
from .mentor_context import build_mentor_messages, mentor_scope

INTERVAL = 3600
MAX_INPUT = 1024 * 1024
MAX_EXPORT = 256 * 1024
MAX_QUESTIONS = 256  # per transport part, not a curriculum ceiling
MAX_ANSWER = 4000
_ID = re.compile(r"mentor-batch-[a-z0-9-]{1,55}")
_QID = re.compile(r"q[0-9]{1,6}")



def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_bytes(path, limit=MAX_INPUT):
    if path.is_symlink():
        raise PermissionError("Ссылка вместо файла учебного контура")
    with path.open("rb") as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Превышен размер учебной записи")
    return raw


def read_obj(path):
    obj = json.loads(read_bytes(path))
    if not isinstance(obj, dict):
        raise ValueError("Ожидается объект")
    return obj


def atomic(path, obj):
    atomic_bytes(path, encode(obj))


def atomic_bytes(path, raw):
    if len(raw) > MAX_INPUT or path.is_symlink():
        raise ValueError("Недопустимая учебная запись")
    fd, name = tempfile.mkstemp(prefix=".mentor-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_batch(raw, batch_id, expected):
    if digest(raw) != expected:
        raise PermissionError("Пакет не совпадает с локально утверждённым SHA-256")
    obj = json.loads(raw)
    if (not isinstance(obj, dict) or obj.get("schema") != "ai-mentor.batch.v1"
            or obj.get("source") != "AI-mentor" or obj.get("batch_id") != batch_id
            or obj.get("mode") != "sequential" or obj.get("status") != "not_run"
            or obj.get("exchange_interval_seconds") != INTERVAL):
        raise ValueError("Некорректный пакет")
    questions = obj.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError("Разбейте пакет на транспортные части")
    seen = set()
    result = []
    for row in questions:
        if not isinstance(row, dict):
            raise ValueError("Некорректная строка вопроса")
        qid, question = row.get("id"), row.get("question")
        if (not isinstance(qid, str) or not _QID.fullmatch(qid) or qid in seen
                or not isinstance(question, str) or not question.strip() or len(question) > 4000):
            raise ValueError("Некорректный или повторный вопрос")
        seen.add(qid)
        result.append((qid, question))
    return result


def automatic_export_safe(raw):
    """Ограниченный экспорт учебных ответов; сомнительные строки остаются локально."""
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict)
                or set(data) != {"schema", "batch_id", "batch_sha256", "responses"}
                or data.get("schema") != "ai-mentor.responses.v1"):
            return False
        rows = data.get("responses")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_QUESTIONS:
            return False
        expected = {"id", "answer", "timestamp", "revision", "model", "duration_ms", "status", "source"}
        # Это консервативный дополнительный фильтр, не доказательство отсутствия личных данных.
        secret_pattern = re.compile(
            r"(?i)(?:gh[pousr]_[a-z0-9_]{12,}|github_pat_[a-z0-9_]{12,}|"
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[a-z0-9_-]{16,}|"
            r"bearer\s+[a-z0-9_.-]{12,}|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|"
            r"(?:password|passwd|пароль|api[_ -]?key|токен)\s*[:=]\s*\S+)"
        )
        for row in rows:
            if (not isinstance(row, dict) or set(row) != expected
                    or row.get("source") != "leshy_model" or row.get("status") != "answered"
                    or row.get("model") != "not_confirmed"
                    or not isinstance(row.get("id"), str) or not _QID.fullmatch(row["id"])
                    or not isinstance(row.get("answer"), str)
                    or not 1 <= len(row["answer"]) <= MAX_ANSWER
                    or secret_pattern.search(row["answer"])
                    or not isinstance(row.get("revision"), str)
                    or not re.fullmatch(r"\d+\.\d+\.\d+|not_confirmed", row["revision"])
                    or not isinstance(row.get("timestamp"), str)
                    or len(row["timestamp"]) > 64
                    or type(row.get("duration_ms")) is not int
                    or row["duration_ms"] < 0):
                return False
            if datetime.fromisoformat(row["timestamp"]).tzinfo is None:
                return False
        return True
    except (ValueError, TypeError, OverflowError):
        return False


class MentorTransportUnavailable(RuntimeError):
    """Временный сбой доставки, не отказ в авторизации."""


def is_transient_transport_error(exc):
    if isinstance(exc, error.HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    if isinstance(exc, error.URLError):
        # Ошибки доверия TLS не должны маскироваться как временная недоступность.
        return not isinstance(exc.reason, ssl.SSLError)
    return isinstance(exc, (TimeoutError, ConnectionError))


class BatchRunner:
    def __init__(self, root, app, stop_event, gh_call):
        self.root = Path(root)
        self.app = app
        self.stop = stop_event
        self.gh_call = gh_call
        self.last_performance = None

    def config(self):
        return read_obj(self.root / "config.json")

    def still_allowed(self, config):
        current = self.config()
        keys = ("repository", "batch_id", "batch_sha256")
        return (not self.stop.is_set() and current.get("enabled") is True
                and all(current.get(k) == config.get(k) for k in keys))

    @staticmethod
    def uses_local_delivery(repo):
        return repo.casefold() == "jaster351/ai-mentor" and os.name == "nt"

    def cycle(self, config):
        self.last_performance = None
        batch_id, expected, repo = (config.get(k) for k in
                                    ("batch_id", "batch_sha256", "repository"))
        if (not isinstance(batch_id, str) or not _ID.fullmatch(batch_id)
                or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)):
            return "not_configured"
        token = load_secret(self.root / "github_token.dat")
        if not token:
            return "not_configured"
        local_delivery = self.uses_local_delivery(repo)
        online = True

        def cycle_allowed():
            return (self.still_allowed(config)
                    and load_secret(self.root / "github_token.dat") == token)

        def gh(path="", payload=None, method="GET"):
            if not self.still_allowed(config) or load_secret(self.root / "github_token.dat") != token:
                raise PermissionError("Локальная авторизация изменилась")
            if not online:
                raise MentorTransportUnavailable("Нет связи с GitHub")
            try:
                return self.gh_call(repo, token, path, payload, method)
            except (error.URLError, TimeoutError, ConnectionError) as exc:
                if local_delivery and is_transient_transport_error(exc):
                    raise MentorTransportUnavailable("Временный сбой GitHub") from exc
                raise

        try:
            remote = gh()
            if remote.get("private") is not True:
                raise PermissionError("Только приватный репозиторий")
            if local_delivery and (remote.get("id") != 1470788052
                                   or remote.get("full_name", "").casefold() != "jaster351/ai-mentor"):
                raise PermissionError("Репозиторий наставника не совпадает")
        except MentorTransportUnavailable:
            online = False
        if not self.still_allowed(config):
            return "disabled"
        if local_delivery:
            # Штатный обновлятор доставляет только проверенные данные, без сборки кода.
            delivery = self.root / (batch_id + ".delivery.json")
            if not delivery.exists() and not delivery.is_symlink():
                return "waiting_delivery"
            raw = read_bytes(delivery)
        else:
            # Обратная совместимость для других разрешённых платформ и репозиториев.
            item = gh("/contents/batches/" + batch_id + ".json?ref=main")
            raw = base64.b64decode(item["content"])
        questions = validate_batch(raw, batch_id, expected)
        if any(token in question for _, question in questions):
            raise PermissionError("Вопрос содержит секрет")
        manifest_path = self.root / (batch_id + ".manifest.json")
        manifest = {"batch_id": batch_id, "batch_sha256": expected, "repository": repo,
                    "question_ids": [qid for qid, _ in questions]}
        if manifest_path.exists():
            if read_obj(manifest_path) != manifest:
                raise ValueError("Изменение уже принятого пакета: необходим новый ID")
        else:
            atomic(manifest_path, manifest)
        for qid, question in questions:
            rid = batch_id + "-" + qid
            path = self.root / (rid + ".question.json")
            if not path.exists():
                atomic(path, {"request_id": rid, "batch_sha256": expected,
                              "question": question, "timestamp": now()})
            else:
                existing = read_obj(path)
                if (existing.get("request_id"), existing.get("batch_sha256"), existing.get("question")) != (rid, expected, question):
                    raise ValueError("Локальный вопрос не совпадает с пакетом")
        # Квитанция относится к проверенному и сохранённому пакету, а не к генерации ответа.
        if not self.still_allowed(config):
            return "disabled"
        try:
            self.publish_receipt(config, gh, questions)
            # Сначала подтверждаем публикацию ответов, затем принимаем их оценку.
            if self.still_allowed(config):
                self.publish(config, gh, token, questions)
        except MentorTransportUnavailable:
            online = False
        if not self.still_allowed(config):
            return "disabled"
        try:
            if online:
                load = self.receive_review(config, gh, token)
            else:
                load = self.receive_review(config, gh, token, allow_network=False)
        except MentorTransportUnavailable:
            online = False
            load = self.receive_review(config, gh, token, allow_network=False)
        quota = load["quota"]
        deadline = time.monotonic() + 0.85 * INTERVAL
        history = []
        completed = 0
        performance = CyclePerformance(self.root.parent)
        stop_reason = "batch_end"
        for qid, question in questions:
            if not cycle_allowed() or time.monotonic() >= deadline:
                stop_reason = "disabled_or_budget"
                break
            rid = batch_id + "-" + qid
            path = self.root / (rid + ".json")
            if path.exists():
                saved = read_obj(path)
                if (saved.get("request_id") != rid or saved.get("batch_sha256") != expected
                        or saved.get("question_sha256") != digest(question.encode("utf-8"))):
                    raise ValueError("Локальный результат другого пакета")
                if saved.get("status") == "answered" and saved.get("source") == "leshy_model":
                    answer = saved.get("answer")
                    if not isinstance(answer, str) or not answer or len(answer) > MAX_ANSWER:
                        raise ValueError("Некорректный сохранённый ответ")
                    history.append((question, answer))
                    continue
                # Crash during inference or failure: do not pretend it succeeded.
                stop_reason = "outcome_unknown" if saved.get("status") == "sent" else "error"
                break
            if completed >= quota:
                stop_reason = "quota"
                break
            # Native local model only for the first integration. Never invoke echo,
            # ordinary app.chat, an external Ollama endpoint, or tools.
            if (self.app.settings.llm_backend != "native"
                    or isinstance(self.app.llm, EchoLLM)
                    or not self.app.model_path.is_file()):
                stop_reason = "model_unavailable"
                break
            if self.app.conversation_activity.snapshot().get("active_conversations", 1):
                stop_reason = "interactive_priority"
                break
            scheduler = get_inference_scheduler(self.app)
            scheduler_status = scheduler.status()
            if scheduler_status.get("active") or scheduler_status.get("waiting"):
                stop_reason = "interactive_priority"
                break
            messages = build_mentor_messages(history, question)
            try:
                with scheduler.slot(source="ai-mentor", priority=PRIORITY_BACKGROUND,
                                    cancel_event=self.stop, timeout=0.25):
                    if (not cycle_allowed()
                            or self.app.conversation_activity.snapshot().get("active_conversations", 1)):
                        stop_reason = "interactive_priority_or_disabled"
                        break
                    started = time.monotonic()
                    record = {"request_id": rid, "batch_sha256": expected,
                              "question_sha256": digest(question.encode("utf-8")),
                              "source": "leshy_model", "status": "sent", "timestamp": now()}
                    # Exclusive creation is the last guard against duplicate dispatch.
                    with path.open("x", encoding="utf-8") as f:
                        json.dump(record, f, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    try:
                        cpu_before = cpu_clock()
                        with mentor_scope() as context_samples, capture_mentor_inference() as measured:
                            answer = finalize_model_output(self.app.llm.chat(messages))
                        for sample in context_samples:
                            event("mentor_context_budget", **sample)
                        if not answer or len(answer) > MAX_ANSWER or token in answer:
                            raise ValueError("Недопустимый ответ")
                        from .app import VERSION
                        record.update(status="answered", answer=answer, timestamp=now(),
                                      revision=VERSION, model="not_confirmed",
                                      duration_ms=int((time.monotonic() - started) * 1000))
                        atomic(path, record)
                        try:
                            performance.add(measured, record["duration_ms"] / 1000, cpu_before)
                            performance.add_context(context_samples)
                        except Exception as exc:
                            event("mentor_performance_sample_failed", error_type=type(exc).__name__)
                    except Exception as exc:
                        record.update(status="error", timestamp=now(), error_type=type(exc).__name__)
                        record.pop("answer", None)
                        atomic(path, record)
                        event("mentor_batch_inference_failed", error_type=type(exc).__name__)
                        stop_reason = "error"
                        break
            except (TimeoutError, InterruptedError):
                stop_reason = "queue_interrupted"
                break
            completed += 1
            history.append((question, answer))
        if completed >= quota and stop_reason == "batch_end":
            stop_reason = "quota"
        metrics = {"batch_id": batch_id, "quota": quota, "completed": completed,
                   "stop_reason": stop_reason, "timestamp": now()}
        try:
            self.last_performance = {"schema": "ai-mentor.performance.v1",
                                     "source": "leshy_runtime",
                                     "batch_id": batch_id, "batch_sha256": expected,
                                     "timestamp": now(), "cycle": metrics,
                                     "measurements": performance.summary()}
            atomic(self.root / "performance-latest.json", self.last_performance)
        except Exception as exc:
            event("mentor_performance_local_failed", error_type=type(exc).__name__)
        # Do not erase the last productive capacity sample with an empty review cycle.
        if completed or not (self.root / "cycle-metrics.json").exists():
            atomic(self.root / "cycle-metrics.json", metrics)
        if cycle_allowed():
            self.prepare_export(config, questions)
            if online and self.config().get("auto_publish") is True:
                try:
                    self.publish(config, gh, token, questions)
                except MentorTransportUnavailable:
                    online = False
        if stop_reason in {"error", "outcome_unknown"}:
            return stop_reason
        if not self.still_allowed(config):
            return "disabled"
        if not cycle_allowed():
            return "waiting"
        if not online:
            return "waiting_connection"
        if (self.root / "export.json").exists():
            return "awaiting_review"
        return "published" if stop_reason == "batch_end" else "waiting"

    def publish_receipt(self, config, gh, questions):
        """Подтверждение сохранения вопросов без передачи их текста или личных данных."""
        batch_id = config["batch_id"]
        expected = config["batch_sha256"]
        identity = {"schema": "ai-mentor.receipt.v1", "source": "leshy_runtime",
                    "status": "received", "batch_id": batch_id,
                    "batch_sha256": expected, "question_count": len(questions)}
        local = self.root / (batch_id + ".receipt.json")
        if local.exists():
            receipt = read_obj(local)
            if any(receipt.get(k) != v for k, v in identity.items()):
                raise ValueError("Квитанция другого пакета")
        else:
            receipt = dict(identity, timestamp=now())
            atomic(local, receipt)
        target = "/contents/receipts/" + batch_id + "-" + expected + ".json"
        try:
            item = gh(target + "?ref=main")
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
            if not self.still_allowed(config):
                return
            try:
                gh(target, {"message": "Квитанция приёма учебного пакета " + batch_id,
                            "branch": "main",
                            "content": base64.b64encode(encode(receipt)).decode("ascii")}, "PUT")
            except error.HTTPError as put_error:
                if put_error.code not in {409, 422}:
                    raise
            item = gh(target + "?ref=main")
        if (item.get("type") != "file" or item.get("encoding") != "base64"
                or not isinstance(item.get("content"), str)):
            raise ValueError("Некорректная удалённая квитанция")
        raw = base64.b64decode("".join(item["content"].split()), validate=True)
        if len(raw) > 4096:
            raise ValueError("Превышен размер квитанции")
        published = json.loads(raw)
        if (not isinstance(published, dict)
                or any(published.get(k) != v for k, v in identity.items())
                or not isinstance(published.get("timestamp"), str)):
            raise PermissionError("Квитанция не совпадает с принятым пакетом")
        try:
            ts = datetime.fromisoformat(published["timestamp"])
            if ts.tzinfo is None:
                raise ValueError("Нет часового пояса")
        except ValueError as exc:
            raise ValueError("Некорректное время квитанции") from exc
        if self.still_allowed(config):
            atomic(self.root / (batch_id + ".receipt-published.json"),
                   {"batch_id": batch_id, "batch_sha256": expected,
                    "receipt_sha256": digest(raw), "timestamp": now()})

    def receive_review(self, config, gh, token, *, allow_network=True):
        from .mentor_review import apply_review
        current = self.config()
        approval = current.get("approved_review_sha256", "")
        remote = current.get("review_download_enabled") is True
        effective = dict(config, approved_review_sha256=approval)

        def apply(cfg, path=None):
            return apply_review(self.root, cfg, read_obj=read_obj,
                                read_bytes=read_bytes, atomic=atomic,
                                digest=digest, now=now, review_path=path)

        if not self.still_allowed(config) or not allow_network:
            return apply(dict(effective, approved_review_sha256=""))
        if not remote or not approval:
            return apply(effective)
        if not isinstance(approval, str) or not re.fullmatch(r"[0-9a-f]{64}", approval):
            raise ValueError("Некорректный SHA-256 оценки")
        state = apply(dict(effective, approved_review_sha256=""))
        if approval in state.get("applied", []):
            return state
        path = self.root / ("review-" + approval + ".json")
        if not path.exists():
            try:
                item = gh("/contents/reviews/" + approval + ".json?ref=main")
            except error.HTTPError as exc:
                if exc.code != 404:
                    raise
                return state
            if (item.get("type") != "file" or item.get("encoding") != "base64"
                    or not isinstance(item.get("content"), str)):
                raise ValueError("Некорректный файл оценки")
            raw = base64.b64decode("".join(item["content"].split()), validate=True)
            if len(raw) > MAX_EXPORT or digest(raw) != approval or token.encode("utf-8") in raw:
                raise PermissionError("Файл оценки не соответствует разрешению")
            if not self.still_allowed(config):
                return state
            latest = self.config()
            if latest.get("review_download_enabled") is not True or latest.get("approved_review_sha256") != approval:
                return state
            atomic_bytes(path, raw)
        if not self.still_allowed(config):
            return state
        latest = self.config()
        if latest.get("review_download_enabled") is not True or latest.get("approved_review_sha256") != approval:
            return state
        # Повторно проверяем сохранённые байты; оценка не является командой.
        return apply(effective, path)

    def prepare_export(self, config, questions):
        path = self.root / "export.json"
        if path.exists():
            return
        payload = {"schema": "ai-mentor.responses.v1", "batch_id": config["batch_id"],
                   "batch_sha256": config["batch_sha256"], "responses": []}
        for qid, question in questions:
            rid = config["batch_id"] + "-" + qid
            state = self.root / (rid + ".json")
            if not state.exists():
                break
            row = read_obj(state)
            if (row.get("status") != "answered" or row.get("source") != "leshy_model"
                    or row.get("request_id") != rid or row.get("batch_sha256") != config["batch_sha256"]
                    or row.get("question_sha256") != digest(question.encode("utf-8"))):
                break
            answer = row.get("answer")
            if not isinstance(answer, str) or not answer or len(answer) > MAX_ANSWER:
                raise ValueError("Некорректный ответ для экспорта")
            marker = self.root / (rid + ".published.json")
            if marker.exists() and read_obj(marker).get("answer_sha256") == digest(answer.encode("utf-8")):
                continue
            payload["responses"].append({"id": qid, "answer": answer,
                "timestamp": row["timestamp"], "revision": row.get("revision", "not_confirmed"),
                "model": "not_confirmed", "duration_ms": row.get("duration_ms"),
                "status": "answered", "source": "leshy_model"})
            if len(encode(payload)) > MAX_EXPORT:
                payload["responses"].pop()
                break
        if payload["responses"]:
            atomic(path, payload)

    def publish(self, config, gh, token, questions):
        path = self.root / "export.json"
        if not path.exists():
            return
        raw = read_bytes(path, MAX_EXPORT)
        def allowed_export():
            approved = self.config()
            if (not self.still_allowed(config)
                    or not token or load_secret(self.root / "github_token.dat") != token):
                return False
            if approved.get("approved_export_sha256") == digest(raw):
                return True
            return (config.get("repository") == "jaster351/AI-mentor"
                    and config.get("auto_publish") is True
                    and approved.get("auto_publish") is True
                    and approved.get("auto_queue") is True
                    and token.startswith("ghu_")
                    and automatic_export_safe(raw))

        if not allowed_export():
            return
        obj = json.loads(raw)
        if (obj.get("schema") != "ai-mentor.responses.v1"
                or obj.get("batch_id") != config["batch_id"]
                or obj.get("batch_sha256") != config["batch_sha256"]
                or not self.still_allowed(config)):
            raise PermissionError("Экспорт другого пакета")
        qmap = dict(questions)
        rows = obj.get("responses")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Пустой экспорт")
        seen = set()
        for row in rows:
            qid = row["id"]
            if qid not in qmap or qid in seen:
                raise ValueError("Некорректный ID экспорта")
            seen.add(qid)
            rid = config["batch_id"] + "-" + qid
            local = read_obj(self.root / (rid + ".json"))
            if (local.get("status") != "answered" or local.get("source") != "leshy_model"
                    or local.get("batch_sha256") != config["batch_sha256"]
                    or row.get("answer") != local.get("answer")
                    or not isinstance(row.get("answer"), str)
                    or token in row["answer"]):
                raise PermissionError("Несовпадение локального результата")
        target = "/contents/responses/" + config["batch_id"] + "-" + digest(raw) + ".json"
        try:
            result = gh(target + "?ref=main")
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
            result = None
        if result is None:
            if not allowed_export() or gh().get("private") is not True or not allowed_export():
                return
            gh(target, {"message": "Проверенный учебный пакет " + config["batch_id"],
                        "branch": "main", "content": base64.b64encode(raw).decode("ascii")}, "PUT")
            result = gh(target + "?ref=main")
        if base64.b64decode(result["content"]) != raw:
            raise RuntimeError("Публикация не подтверждена")
        for row in rows:
            rid = config["batch_id"] + "-" + row["id"]
            atomic(self.root / (rid + ".published.json"), {"request_id": rid,
                   "answer_sha256": digest(row["answer"].encode("utf-8")), "timestamp": now()})
        path.unlink()
