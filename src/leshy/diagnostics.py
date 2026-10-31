from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid
from typing import Any

from .logging_paths import analysis_log_dir as resolve_analysis_log_dir
from .logging_paths import central_log_dir, resolve_brain_root

_LOGGER_NAME = "leshy"
_SESSION_ID = uuid.uuid4().hex[:12]
_CONFIGURED = False
_LOG_DIR: Path | None = None
_ANALYSIS_LOG_DIR: Path | None = None
_HANDLERS: list[logging.Handler] = []
_SECRET_MARKERS = ("token", "secret", "password", "authorization", "api_key", "apikey", "cookie")

_BEARER_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_BOT_RE = re.compile(r"(?i)(/bot)(\d+:[A-Za-z0-9_-]+)")
_QUERY_SECRET_RE = re.compile(
    r"(?i)((?:token|secret|password|api[_-]?key|authorization|cookie|signature|policy|key-pair-id|x-amz-signature|x-amz-credential|x-amz-security-token)=)([^&\s\"]+)"
)
_HOME = str(Path.home())


def _sanitize_text(value: str) -> str:
    text = str(value)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    text = _BOT_RE.sub(r"\1<redacted>", text)
    text = _QUERY_SECRET_RE.sub(r"\1<redacted>", text)
    if _HOME not in {"", "/", "\\"}:
        text = text.replace(_HOME, "<home>")
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _sanitize_text(super().format(record))


def close_diagnostics() -> None:
    global _CONFIGURED, _LOG_DIR, _ANALYSIS_LOG_DIR, _HANDLERS
    logger = logging.getLogger(_LOGGER_NAME)
    root = logging.getLogger()
    handlers = list(_HANDLERS)
    _HANDLERS = []
    for handler in handlers:
        for target in (logger, root):
            try:
                target.removeHandler(handler)
            except Exception:
                pass
        try:
            handler.flush()
        except Exception:
            pass
        try:
            handler.close()
        except Exception:
            pass
    _CONFIGURED = False
    _LOG_DIR = None
    _ANALYSIS_LOG_DIR = None


def configure_diagnostics(base_dir: Path, version: str) -> logging.Logger:
    global _CONFIGURED, _LOG_DIR, _ANALYSIS_LOG_DIR, _HANDLERS
    log_dir = central_log_dir(Path(base_dir))
    analysis_dir = resolve_analysis_log_dir()
    if analysis_dir == log_dir:
        analysis_dir = None
    if _CONFIGURED and _LOG_DIR == log_dir and _ANALYSIS_LOG_DIR == analysis_dir:
        event("process_reconfigured", version=version, pid=os.getpid(), session=_SESSION_ID)
        return logging.getLogger(_LOGGER_NAME)
    if _CONFIGURED:
        close_diagnostics()
    _LOG_DIR = log_dir
    _ANALYSIS_LOG_DIR = analysis_dir
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    all_handler = RotatingFileHandler(log_dir / "leshy.log", maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8")
    all_handler.setLevel(logging.INFO)
    all_handler.setFormatter(formatter)
    problem_handler = RotatingFileHandler(log_dir / "problems.log", maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8")
    problem_handler.setLevel(logging.WARNING)
    problem_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [all_handler, problem_handler]
    if analysis_dir is not None:
        analysis_handler = RotatingFileHandler(
            analysis_dir / "leshy-auto.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=4,
            encoding="utf-8",
        )
        analysis_handler.setLevel(logging.INFO)
        analysis_handler.setFormatter(formatter)
        handlers.append(analysis_handler)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in handlers:
        logger.addHandler(handler)
        root.addHandler(handler)
    logging.captureWarnings(True)
    _HANDLERS = handlers
    _CONFIGURED = True
    _install_exception_hooks(logger)
    event(
        "process_start",
        version=version,
        pid=os.getpid(),
        session=_SESSION_ID,
        runtime_log_root="LeshyBrain/logs",
        analysis_mirror=analysis_dir is not None,
    )
    return logger


def log_dir() -> Path | None:
    return _LOG_DIR


def analysis_log_dir() -> Path | None:
    return _ANALYSIS_LOG_DIR


def _redact_dict(value: dict[Any, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if any(marker in key_text.lower() for marker in _SECRET_MARKERS):
            result[key_text] = "<redacted>"
        else:
            result[key_text] = _safe(item)
    return result


def _safe(value: Any) -> Any:
    if isinstance(value, Path):
        return _sanitize_text(str(value))
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return _redact_dict(value)
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    return _sanitize_text(repr(value))


def event(name: str, **fields: Any) -> None:
    payload = {"event": name, "session": _SESSION_ID, "pid": os.getpid(), "thread": threading.current_thread().name, **_redact_dict(fields)}
    logging.getLogger(_LOGGER_NAME).info(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def problem(name: str, message: str, **fields: Any) -> None:
    payload = {"event": name, "session": _SESSION_ID, "pid": os.getpid(), "thread": threading.current_thread().name, "message": _sanitize_text(message), **_redact_dict(fields)}
    logging.getLogger(_LOGGER_NAME).warning(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def exception(name: str, exc: BaseException, **fields: Any) -> None:
    payload = {
        "event": name,
        "session": _SESSION_ID,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "error_type": type(exc).__name__,
        "error": _sanitize_text(str(exc)),
        "traceback": _sanitize_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))),
        **_redact_dict(fields),
    }
    logging.getLogger(_LOGGER_NAME).error(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_snapshot(base_dir: Path, **state: Any) -> Path:
    brain_root = resolve_brain_root(Path(base_dir))
    path = central_log_dir(brain_root) / "diagnostics.json"
    payload = {"timestamp": time.time(), "session": _SESSION_ID, "pid": os.getpid(), **_redact_dict(state)}
    _write_json_atomic(path, payload)
    analysis_dir = _ANALYSIS_LOG_DIR or resolve_analysis_log_dir()
    if analysis_dir is not None and analysis_dir != path.parent:
        _write_json_atomic(analysis_dir / "diagnostics-auto.json", payload)
    return path


def recent_problems(limit: int = 100) -> list[str]:
    if _LOG_DIR is None:
        return []
    path = _LOG_DIR / "problems.log"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, min(int(limit), 500)):]


def _install_exception_hooks(logger: logging.Logger) -> None:
    previous = sys.excepthook
    if getattr(previous, "_leshy_hook", False):
        return
    def sys_hook(exc_type, exc, tb):
        try:
            payload = {"event": "unhandled_exception", "session": _SESSION_ID, "pid": os.getpid(), "thread": threading.current_thread().name, "error_type": getattr(exc_type, "__name__", str(exc_type)), "error": _sanitize_text(str(exc)), "traceback": _sanitize_text("".join(traceback.format_exception(exc_type, exc, tb)))}
            logger.critical(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        finally:
            previous(exc_type, exc, tb)
    sys_hook._leshy_hook = True  # type: ignore[attr-defined]
    sys.excepthook = sys_hook
