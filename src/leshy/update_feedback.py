"""Проверка обновлений из Windows-трея с достоверным результатом.

Задача Windows выполняется скрыто, поэтому состояние записывается отдельным
процессом планировщика в .autobuild/update-status.json. Каждый ручной запрос
имеет собственный идентификатор: старые результаты никогда не считаются новыми.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable
from uuid import uuid4


_LOG = logging.getLogger(__name__)
_TASK = "Leshy AI - Git Build (15 min)"
_START_TIMEOUT = 100
_MAX_CHECK_SECONDS = 45 * 60


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _utc_time(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None


def render_state(payload: dict) -> tuple[str, bool]:
    """Возвращает точное сообщение и признак завершённой проверки."""
    state = str(payload.get("state", "")).strip()
    version = str(payload.get("version", "")).strip()
    deployed = str(payload.get("deployed_version", "")).strip()
    suffix = (" " + version) if version else ""
    if state == "checking":
        return "Проверяю GitHub и сравниваю версии...", False
    if state == "updating":
        detail = f" (установлена {deployed})" if deployed else ""
        return f"Найдена версия{suffix}{detail}. Выполняется сборка и обновление.", False
    if state == "up_to_date":
        return f"Обновлений нет. Леший{suffix} соответствует GitHub main.", True
    if state == "updated":
        return f"Леший обновлён до версии{suffix}. Перезапуск выполнен сборщиком.", True
    if state == "busy":
        return "Другая сборка уже выполняется. Проверьте позже.", True
    if state == "error":
        detail = str(payload.get("message", "")).replace("\r", " ").replace("\n", " ").strip()
        if len(detail) > 180:
            detail = detail[:177] + "..."
        return ("Ошибка проверки обновлений: " + detail if detail else
                "Ошибка проверки обновлений. Откройте журнал планировщика."), True
    return "Статус проверки пока неизвестен.", False


class TrayUpdateFeedback:
    """Не блокирует поток трея. Уведомления + доступный статус в меню."""

    def __init__(
        self,
        repository: Path,
        notify: Callable[[str], None],
        changed: Callable[[], None],
    ) -> None:
        self._repository = Path(repository)
        self._auto = self._repository / ".autobuild"
        self._notify = notify
        self._changed = changed
        self._lock = threading.Lock()
        self._working = False
        self.message = "проверка ещё не запускалась"
        self._restore_last_result()

    def _restore_last_result(self) -> None:
        value = _read_json(self._auto / "update-status.json")
        if value is None:
            return
        updated = _utc_time(str(value.get("updated_at", "")))
        if updated is None or (datetime.now(timezone.utc) - updated).total_seconds() > 24 * 3600:
            return
        text, done = render_state(value)
        self.message = text
        if not done and value.get("request_id") and value.get("state") in {"checking", "updating"}:
            # A previous Leshy.exe can exit during deployment. The newly
            # launched process must pick up the result from the scheduled task.
            threading.Thread(
                target=self._watch_existing_request,
                args=(str(value["request_id"]), str(value["state"])),
                name="leshy-update-resume-feedback",
                daemon=True,
            ).start()

    def _watch_existing_request(self, request_id: str, initial_state: str) -> None:
        deadline = time.monotonic() + _MAX_CHECK_SECONDS
        previous = initial_state
        while time.monotonic() < deadline:
            payload = _read_json(self._auto / "update-status.json")
            if payload is None or payload.get("request_id") != request_id:
                return
            stamp = _utc_time(str(payload.get("updated_at", "")))
            if stamp is None or (datetime.now(timezone.utc) - stamp).total_seconds() > _MAX_CHECK_SECONDS:
                return
            state = str(payload.get("state", ""))
            message, done = render_state(payload)
            if state != previous:
                self._report(message, notify=True)
                previous = state
            if done:
                return
            time.sleep(3)

    def _report(self, message: str, *, notify: bool) -> None:
        self.message = message
        try:
            self._changed()
        except Exception:
            _LOG.exception("Не удалось обновить меню трея")
        if notify:
            try:
                self._notify(message)
            except Exception:
                _LOG.exception("Не удалось показать уведомление о проверке обновлений")

    def start(self) -> bool:
        with self._lock:
            if self._working:
                self._report("Проверка обновлений уже выполняется.", notify=True)
                return False
            self._working = True
        self._report("Проверяю обновления... Ожидаю запуска планировщика Windows.", notify=True)
        threading.Thread(target=self._run, name="leshy-update-feedback", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            self._execute()
        except Exception as exc:
            self._report(f"Не удалось запустить проверку обновлений: {exc}", notify=True)
        finally:
            with self._lock:
                self._working = False

    def _execute(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Проверка через Windows Task Scheduler недоступна")
        if not (self._repository / ".git").exists():
            raise FileNotFoundError("Папка Git-репозитория Лешего не найдена")
        self._auto.mkdir(parents=True, exist_ok=True)
        request_id = str(uuid4())
        started = datetime.now(timezone.utc)
        request = {
            "request_id": request_id,
            "created_at": started.isoformat(),
        }
        target = self._auto / "update-request.json"
        temp = self._auto / ("update-request-" + request_id + ".tmp")
        try:
            temp.write_text(json.dumps(request, ensure_ascii=True), encoding="utf-8")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

        schtasks = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "schtasks.exe"
        try:
            run = subprocess.run(
                [str(schtasks), "/Run", "/TN", _TASK],
                capture_output=True, text=True, errors="replace",
                timeout=18, creationflags=0x08000000,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            target.unlink(missing_ok=True)
            raise RuntimeError("Не удалось запустить задачу Windows: " + str(exc)) from exc

        if run.returncode != 0:
            target.unlink(missing_ok=True)
            detail = (run.stderr or run.stdout or "").strip().replace("\r", " ").replace("\n", " ")
            detail = detail[:240]
            raise RuntimeError(
                "Задача обновлений не запущена. Проверьте её наличие и права доступа. " + detail
            )
        self._report("Задача запущена. Сравниваю GitHub с установленной версией...", notify=False)

        status_path = self._auto / "update-status.json"
        last_state = None
        saw_start = False
        deadline = time.monotonic() + _MAX_CHECK_SECONDS
        while time.monotonic() < deadline:
            payload = _read_json(status_path)
            if payload is not None and payload.get("request_id") == request_id:
                stamp = _utc_time(str(payload.get("updated_at", "")))
                if stamp is not None and stamp >= started:
                    state = str(payload.get("state", ""))
                    if state != last_state:
                        message, complete = render_state(payload)
                        self._report(message, notify=True)
                        last_state = state
                    else:
                        _, complete = render_state(payload)
                    saw_start = True
                    if complete:
                        return
            if not saw_start and time.monotonic() > deadline - _MAX_CHECK_SECONDS + _START_TIMEOUT:
                raise TimeoutError(
                    "Планировщик не подтвердил начало проверки за 100 секунд. "
                    "Проверьте .autobuild/logs/scheduled-build.log."
                )
            time.sleep(2)
        raise TimeoutError(
            "Проверка длится более 45 минут. Статус неизвестен; "
            "проверьте .autobuild/logs/scheduled-build.log."
        )
