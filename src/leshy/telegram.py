from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Iterable
from urllib import parse, request, error


class TelegramBridge:
    def __init__(self, app, brain_dir: Path):
        self.app = app
        self.brain_dir = Path(brain_dir)
        self.secrets_dir = self.brain_dir / "secrets"
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.token_path = self.secrets_dir / "telegram_token.txt"
        self.allowed_path = self.secrets_dir / "telegram_allowed_users.txt"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_error = ""
        self._offset = 0

    def _token(self) -> str:
        if not self.token_path.is_file():
            return ""
        return self.token_path.read_text(encoding="utf-8").strip()

    def _allowed(self) -> set[str]:
        if not self.allowed_path.is_file():
            return set()
        return {line.strip() for line in self.allowed_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    def configure(self, token: str, allowed_users: Iterable[str | int]) -> None:
        token = str(token or "").strip()
        if token:
            self.token_path.write_text(token, encoding="utf-8")
        allowed = sorted({str(value).strip() for value in allowed_users if str(value).strip()})
        self.allowed_path.write_text("\n".join(allowed) + ("\n" if allowed else ""), encoding="utf-8")

    def status(self) -> dict:
        return {
            "configured": bool(self._token()),
            "allowed_users": len(self._allowed()),
            "running": bool(self._thread and self._thread.is_alive()),
            "last_error": self._last_error,
        }

    def start(self) -> bool:
        if not self._token():
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-telegram", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _api(self, method: str, payload: dict | None = None, timeout: int = 40):
        token = self._token()
        if not token:
            raise RuntimeError("Telegram token не настроен")
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data, headers=headers, method="POST" if data else "GET")
        with request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description") or "Telegram API error"))
        return body.get("result")

    def _send(self, chat_id: int | str, text: str) -> None:
        self._api("sendMessage", {"chat_id": chat_id, "text": text[:4000]})

    def _handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = str(message.get("text") or "").strip()
        chat_id = chat.get("id")
        user_id = str(sender.get("id") or "")
        if not text or chat_id is None or not user_id:
            return
        allowed = self._allowed()
        if not allowed or user_id not in allowed:
            self._send(chat_id, "Доступ к Лешему не настроен для этого Telegram ID.")
            return
        if text == "/status":
            status = self.app.status()
            self._send(chat_id, f"Леший {status['version']}\nМодель: {status['model']}\nСамообучение: {'ВКЛ' if status['learning_enabled'] else 'ВЫКЛ'}")
            return
        if text == "/learning_on":
            self.app.set_learning(True)
            self._send(chat_id, "Самообучение включено.")
            return
        if text == "/learning_off":
            self.app.set_learning(False)
            self._send(chat_id, "Самообучение выключено.")
            return
        try:
            result = self.app.chat(text, owner_id=f"telegram:{user_id}", session_id=f"telegram:{chat_id}:{user_id}")
            self._send(chat_id, result["answer"])
        except Exception as exc:
            self._send(chat_id, f"Не удалось получить ответ Лешего: {exc}")

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                query = parse.urlencode({"timeout": 30, "offset": self._offset, "allowed_updates": json.dumps(["message"])} )
                result = self._api("getUpdates?" + query, timeout=40) or []
                for update in result:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    message = update.get("message")
                    if isinstance(message, dict):
                        self._handle_message(message)
                self._last_error = ""
                backoff = 2
            except (error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                self._last_error = str(exc)[:500]
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as exc:  # pragma: no cover
                self._last_error = str(exc)[:500]
                self._stop.wait(5)
