from __future__ import annotations

import html
import json
import os
from pathlib import Path
import queue
import re
import threading
from typing import Iterable
from urllib import parse, request, error

from .diagnostics import event, problem
from .secret_store import load_secret, save_secret


_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)
_ADMIN_COMMANDS = {"/learning_on", "/learning_off"}


def _telegram_answer_html(text: str) -> str:
    """Hide model reasoning behind Telegram's click-to-reveal spoiler UI."""
    value = str(text or "")
    parts: list[str] = []
    offset = 0
    for match in _THINK_RE.finditer(value):
        visible = value[offset:match.start()]
        if visible:
            parts.append(html.escape(visible))
        thought = str(match.group(1) or "").strip()
        if thought:
            parts.append("<tg-spoiler>" + html.escape(thought) + "</tg-spoiler>")
        offset = match.end()
    tail = value[offset:]
    lower_tail = tail.lower()
    open_index = lower_tail.find("<think>")
    if open_index >= 0:
        visible = tail[:open_index]
        thought = tail[open_index + len("<think>"):].replace("</think>", "").strip()
        if visible:
            parts.append(html.escape(visible))
        if thought:
            parts.append("<tg-spoiler>" + html.escape(thought) + "</tg-spoiler>")
    elif tail:
        parts.append(html.escape(tail.replace("</think>", "")))
    return "".join(parts).strip()


class TelegramBridge:
    def __init__(self, app, brain_dir: Path):
        self.app = app
        self.brain_dir = Path(brain_dir)
        self.secrets_dir = self.brain_dir / "secrets"
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.token_path = self.secrets_dir / "telegram_token.dat"
        self.legacy_token_path = self.secrets_dir / "telegram_token.txt"
        self.allowed_path = self.secrets_dir / "telegram_allowed_users.txt"
        self.access_path = self.secrets_dir / "telegram_access.json"
        self._thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=200)
        self._last_error = ""
        self._offset = 0

    def _token(self) -> str:
        try:
            value = load_secret(self.token_path)
            if value:
                return value
            if self.legacy_token_path.is_file():
                legacy = self.legacy_token_path.read_text(encoding="utf-8").strip()
                if legacy:
                    save_secret(self.token_path, legacy)
                    try:
                        self.legacy_token_path.unlink()
                    except OSError:
                        pass
                    event("telegram_secret_migrated")
                    return legacy
        except Exception as exc:
            problem("telegram_secret_load_failed", str(exc))
        return ""

    def _allowed(self) -> set[str]:
        if not self.allowed_path.is_file():
            return set()
        return {line.strip() for line in self.allowed_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    def _access(self) -> dict:
        default = {"allow_all": False, "owner_id": ""}
        if self.access_path.is_file():
            try:
                raw = json.loads(self.access_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {
                        "allow_all": bool(raw.get("allow_all", False)),
                        "owner_id": str(raw.get("owner_id") or "").strip(),
                    }
            except Exception as exc:
                problem("telegram_access_load_failed", str(exc))
        # Legacy migration convenience: one historical allowed ID is treated as owner.
        allowed = self._allowed()
        if len(allowed) == 1:
            default["owner_id"] = next(iter(allowed))
        return default

    def _save_access(self, *, allow_all: bool, owner_id: str) -> None:
        payload = {"allow_all": bool(allow_all), "owner_id": str(owner_id or "").strip()}
        tmp = self.access_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.access_path)
        try:
            os.chmod(self.access_path, 0o600)
        except OSError:
            pass

    def configure(
        self,
        token: str,
        allowed_users: Iterable[str | int],
        *,
        allow_all: bool = False,
        owner_id: str | int = "",
    ) -> None:
        token = str(token or "").strip()
        if token:
            save_secret(self.token_path, token)
        allowed = sorted({str(value).strip() for value in allowed_users if str(value).strip()})
        owner = str(owner_id or "").strip()
        if not owner and len(allowed) == 1:
            owner = allowed[0]
        if allow_all and not owner:
            raise ValueError("Для режима «отвечать всем» укажите Telegram ID хозяина")
        self.allowed_path.write_text("\n".join(allowed) + ("\n" if allowed else ""), encoding="utf-8")
        try:
            os.chmod(self.allowed_path, 0o600)
        except OSError:
            pass
        self._save_access(allow_all=bool(allow_all), owner_id=owner)
        event(
            "telegram_configured",
            allowed_users=len(allowed),
            token_present=bool(self._token()),
            allow_all=bool(allow_all),
            owner_configured=bool(owner),
        )

    def status(self) -> dict:
        access = self._access()
        return {
            "configured": bool(self._token()),
            "allowed_users": len(self._allowed()),
            "allow_all": bool(access["allow_all"]),
            "owner_id": str(access["owner_id"]),
            "owner_configured": bool(access["owner_id"]),
            "running": bool(self._thread and self._thread.is_alive()),
            "worker_running": bool(self._worker_thread and self._worker_thread.is_alive()),
            "queue_size": self._queue.qsize(),
            "last_error": self._last_error,
        }

    def start(self) -> bool:
        if not self._token():
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._worker_thread = threading.Thread(target=self._worker, name="leshy-telegram-worker", daemon=True)
        self._worker_thread.start()
        self._thread = threading.Thread(target=self._run, name="leshy-telegram-poll", daemon=True)
        self._thread.start()
        event("telegram_started", polling_timeout_seconds=10)
        return True

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        event("telegram_stopped")

    def _api(self, method: str, payload: dict | None = None, timeout: float = 20):
        token = self._token()
        if not token:
            raise RuntimeError("Telegram token не настроен")
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=data,
            headers=headers,
            method="POST" if data else "GET",
        )
        with request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description") or "Telegram API error"))
        return body.get("result")

    def _send(self, chat_id: int | str, text: str) -> None:
        self._api("sendMessage", {"chat_id": chat_id, "text": text[:4000]}, timeout=10)

    def _send_answer(self, chat_id: int | str, text: str) -> None:
        rendered = _telegram_answer_html(text[:3800])
        self._api(
            "sendMessage",
            {"chat_id": chat_id, "text": rendered or "…", "parse_mode": "HTML"},
            timeout=10,
        )

    def _send_typing(self, chat_id: int | str) -> None:
        try:
            self._api("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=5)
        except Exception:
            pass

    def _is_owner(self, user_id: str) -> bool:
        return bool(user_id and user_id == str(self._access().get("owner_id") or ""))

    def _can_chat(self, user_id: str) -> bool:
        if self._is_owner(user_id):
            return True
        access = self._access()
        if bool(access.get("allow_all")):
            return True
        return user_id in self._allowed()

    def _handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = str(message.get("text") or "").strip()
        chat_id = chat.get("id")
        user_id = str(sender.get("id") or "")
        if not text or chat_id is None or not user_id:
            return
        if not self._can_chat(user_id):
            problem("telegram_access_denied", "Telegram ID не разрешён", telegram_user_id=user_id)
            self._send(chat_id, "Доступ к Лешему не настроен для этого Telegram ID.")
            return

        owner = self._is_owner(user_id)
        command = text.split(maxsplit=1)[0].lower()
        if command in _ADMIN_COMMANDS and not owner:
            problem("telegram_admin_denied", "Административная команда доступна только хозяину", telegram_user_id=user_id)
            self._send(chat_id, "Эта команда доступна только хозяину Лешего.")
            return

        if text == "/status":
            status = self.app.status()
            self._send(
                chat_id,
                f"Леший {status['version']}\nМодель: {status['model']}\n"
                f"Самообучение: {'ВКЛ' if status['learning_enabled'] else 'ВЫКЛ'}\n"
                f"Роль: {'хозяин' if owner else 'пользователь'}",
            )
            return
        if text == "/learning_on":
            self.app.set_learning(True)
            self._send(chat_id, "Самообучение включено.")
            return
        if text == "/learning_off":
            self.app.set_learning(False)
            self._send(chat_id, "Самообучение выключено.")
            return

        self._send_typing(chat_id)
        typing_stop = threading.Event()

        def typing_loop() -> None:
            while not typing_stop.wait(4.0):
                self._send_typing(chat_id)

        typing_thread = threading.Thread(target=typing_loop, name="leshy-telegram-typing", daemon=True)
        typing_thread.start()
        try:
            result = self.app.chat(
                text,
                owner_id=f"telegram:{user_id}",
                session_id=f"telegram:{chat_id}:{user_id}",
            )
            self._send_answer(chat_id, result["answer"])
        except Exception as exc:
            problem("telegram_chat_failed", str(exc), telegram_user_id=user_id)
            self._send(chat_id, f"Не удалось получить ответ Лешего: {exc}")
        finally:
            typing_stop.set()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if message is None:
                return
            try:
                self._handle_message(message)
            except Exception as exc:
                problem("telegram_worker_failed", str(exc))
            finally:
                self._queue.task_done()

    def _run(self) -> None:
        # Telegram long polling returns immediately when a message arrives. The
        # shorter timeout only reduces recovery latency after network problems.
        backoff = 0.5
        while not self._stop.is_set():
            try:
                query = parse.urlencode(
                    {"timeout": 10, "offset": self._offset, "allowed_updates": json.dumps(["message"])}
                )
                result = self._api("getUpdates?" + query, timeout=15) or []
                for update in result:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    message = update.get("message")
                    if isinstance(message, dict):
                        try:
                            self._queue.put_nowait(message)
                        except queue.Full:
                            problem("telegram_queue_full", "Очередь Telegram переполнена")
                self._last_error = ""
                backoff = 0.5
            except (error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                self._last_error = str(exc)[:500]
                problem("telegram_poll_failed", self._last_error, retry_seconds=backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 5.0)
            except Exception as exc:  # pragma: no cover
                self._last_error = str(exc)[:500]
                problem("telegram_unexpected_error", self._last_error)
                self._stop.wait(1.0)
