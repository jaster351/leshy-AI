from __future__ import annotations

import json
import queue
import re
import threading
import time
from urllib import parse, error

from .diagnostics import event, problem
from .realtime_chat import RealtimeChatManager
from .telegram_rich import TelegramBridge as RichTelegramBridge, telegram_html_chunks


_ADMIN_COMMANDS = {"/learning_on", "/learning_off"}
_THINK_RE = re.compile(r"(?is)<think\b[^>]*>.*?(?:</think>|$)")


def _partial_text(text: str, limit: int = 3800) -> str:
    value = _THINK_RE.sub("", str(text or "")).replace("</think>", "").strip()
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value or "…"


class FastTelegramBridge(RichTelegramBridge):
    """Telegram использует тот же потоковый LLM-контур, что и Web UI."""

    # Обновлять текст заметно плавнее, чем раньше, но не на каждый токен,
    # чтобы не упираться в лимиты Telegram Bot API.
    edit_interval_seconds = 0.28
    edit_min_growth = 8
    foreground_grace_seconds = 0.08

    def __init__(self, app, brain_dir):
        super().__init__(app, brain_dir)
        self._realtime = RealtimeChatManager(app)

    def status(self) -> dict:
        value = super().status()
        value.update({
            "realtime_streaming": True,
            "same_generation_pipeline_as_web": True,
            "waits_for_full_answer": False,
            "stream_edit_interval_seconds": self.edit_interval_seconds,
            "callback_approvals": True,
        })
        return value

    def _llm_busy(self) -> bool:
        """Проверить, занят ли сейчас любой resident LLM backend.

        Telegram даёт короткую фору Web UI. Если Web уже генерирует ответ,
        Telegram не пытается одновременно захватить тот же единственный slot.
        """
        llm = getattr(self.app, "llm", None)
        states = list(getattr(llm, "_states", []) or [])
        providers = [getattr(state, "provider", None) for state in states]
        if not providers:
            providers = [llm]
        for provider in providers:
            lock = getattr(provider, "_chat_lock", None)
            try:
                if lock is not None and lock.locked():
                    return True
            except Exception:
                continue
        return False

    def _wait_for_foreground_slot(self) -> None:
        # Небольшая фора почти не ощущается в Telegram, но позволяет Web-запросу,
        # пришедшему одновременно, первым занять локальную модель.
        if self._stop.wait(self.foreground_grace_seconds):
            return
        waited = False
        started = time.monotonic()
        while not self._stop.is_set() and self._llm_busy():
            waited = True
            self._stop.wait(0.05)
        if waited:
            event("telegram_waited_for_foreground_llm", latency_ms=int((time.monotonic() - started) * 1000))

    def _send_stream_message(self, chat_id, text: str, reply_to_message_id: int | None, *, reply_markup: dict | None = None) -> int:
        payload = {"chat_id": chat_id, "text": _partial_text(text)}
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": int(reply_to_message_id), "allow_sending_without_reply": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        result = self._api("sendMessage", payload, timeout=10) or {}
        return int(result.get("message_id") or 0)

    def _edit_stream_message(self, chat_id, telegram_message_id: int, text: str, *, html_mode: bool = False, reply_markup: dict | None = None) -> None:
        if not telegram_message_id:
            return
        payload = {"chat_id": chat_id, "message_id": int(telegram_message_id), "text": text or "…"}
        if html_mode:
            payload["parse_mode"] = "HTML"
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            self._api("editMessageText", payload, timeout=10)
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                problem("telegram_stream_edit_failed", str(exc), chat_id=str(chat_id))

    @staticmethod
    def _approval_keyboard(approval: dict | None) -> dict | None:
        if not approval or not approval.get("id"):
            return None
        aid = str(approval["id"])
        return {
            "inline_keyboard": [[
                {"text": str(approval.get("yes_label") or "Да, удалить"), "callback_data": "osdel:y:" + aid},
                {"text": str(approval.get("no_label") or "Нет, отменить"), "callback_data": "osdel:n:" + aid},
            ]]
        }

    def _finalize_answer(self, chat_id, telegram_message_id: int, answer: str, reply_to_message_id: int | None, *, approval: dict | None = None) -> int:
        chunks = telegram_html_chunks(answer)
        if not chunks:
            chunks = ["…"]
        keyboard = self._approval_keyboard(approval)
        if telegram_message_id:
            self._edit_stream_message(chat_id, telegram_message_id, chunks[0], html_mode=True, reply_markup=keyboard)
            first_id = telegram_message_id
        else:
            payload = {"chat_id": chat_id, "text": chunks[0], "parse_mode": "HTML"}
            if reply_to_message_id:
                payload["reply_parameters"] = {"message_id": int(reply_to_message_id), "allow_sending_without_reply": True}
            if keyboard:
                payload["reply_markup"] = keyboard
            result = self._api("sendMessage", payload, timeout=10) or {}
            first_id = int(result.get("message_id") or 0)
        for rendered in chunks[1:]:
            self._api("sendMessage", {"chat_id": chat_id, "text": rendered, "parse_mode": "HTML"}, timeout=10)
        return first_id

    def _handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = str(message.get("text") or message.get("caption") or "").strip()
        chat_id = chat.get("id")
        user_id = str(sender.get("id") or "")
        message_id = message.get("message_id")
        if not text or chat_id is None or not user_id:
            return

        addressed, address_reason = self._social.addressed_to_leshy(message, bot_id=self._bot_id, bot_username=self._bot_username)
        if self._social.is_group(message) and not addressed:
            event("telegram_group_message_ignored", chat_id=str(chat_id), telegram_user_id=user_id, reason=address_reason)
            return
        if not self._can_chat(user_id):
            problem("telegram_access_denied", "Telegram ID не разрешён", telegram_user_id=user_id)
            if not self._social.is_group(message):
                self._send(chat_id, "Доступ к Лешему не настроен для этого Telegram ID.")
            return

        owner = self._is_owner(user_id)
        command = text.split(maxsplit=1)[0].lower()
        if command in _ADMIN_COMMANDS and not owner:
            self._send(chat_id, "Эта команда доступна только хозяину Лешего.")
            return
        os_control = getattr(self.app, "os_control", None)
        if os_control is not None and os_control.is_request(text) and not owner:
            self._send(chat_id, "Управление операционной системой доступно только хозяину Лешего.")
            return

        if text == "/status":
            status = self.app.status()
            self._send(chat_id, f"Леший {status['version']}\nМодель: {status['model']}\nСамообучение: {'ВКЛ' if status['learning_enabled'] else 'ВЫКЛ'}\nРоль: {'хозяин' if owner else 'пользователь'}\nTelegram realtime: ВКЛ")
            return
        if text == "/learning_on":
            self.app.set_learning(True); self._send(chat_id, "Самообучение включено."); return
        if text == "/learning_off":
            self.app.set_learning(False); self._send(chat_id, "Самообучение выключено."); return

        self._send_typing(chat_id)
        typing_stop = threading.Event()
        def typing_loop() -> None:
            while not typing_stop.wait(4.0):
                self._send_typing(chat_id)
        threading.Thread(target=typing_loop, name="leshy-telegram-typing", daemon=True).start()

        telegram_message_id = 0
        answer = ""
        done_payload: dict = {}
        last_edit = 0.0
        last_sent_len = 0
        first_visible_at = 0.0
        started = time.monotonic()
        owner_key = f"telegram:{user_id}"
        session_key = f"telegram:{chat_id}:{user_id}"
        try:
            self._wait_for_foreground_slot()
            for item in self._realtime.stream(text, owner_id=owner_key, session_id=session_key):
                if item.type == "delta":
                    answer += str(item.data.get("text") or "")
                    visible = _partial_text(answer)
                    now = time.monotonic()
                    if not telegram_message_id and visible != "…":
                        telegram_message_id = self._send_stream_message(chat_id, visible, int(message_id) if message_id else None)
                        first_visible_at = now
                        last_edit = now
                        last_sent_len = len(visible)
                        typing_stop.set()
                        event("telegram_first_token_visible", latency_ms=int((now - started) * 1000), chat_id=str(chat_id))
                    elif telegram_message_id and now - last_edit >= self.edit_interval_seconds and len(visible) - last_sent_len >= self.edit_min_growth:
                        self._edit_stream_message(chat_id, telegram_message_id, visible)
                        last_edit = now
                        last_sent_len = len(visible)
                elif item.type == "done":
                    done_payload = dict(item.data)
                    answer = str(done_payload.get("answer") or answer)
                elif item.type == "error":
                    raise RuntimeError(str(item.data.get("error") or "Ошибка генерации"))

            typing_stop.set()
            approval = done_payload.get("approval") if isinstance(done_payload.get("approval"), dict) else None
            telegram_message_id = self._finalize_answer(chat_id, telegram_message_id, answer or "…", int(message_id) if message_id else None, approval=approval)
            self._social.mark_leshy_answered(message)
            reaction = self._social.reaction_for(message, addressed=addressed, answer=answer)
            self._set_reaction(chat_id, int(message_id) if message_id else None, reaction)
            event(
                "telegram_message_answered_realtime",
                chat_id=str(chat_id), telegram_user_id=user_id,
                latency_ms=int((time.monotonic() - started) * 1000),
                first_visible_ms=(int((first_visible_at - started) * 1000) if first_visible_at else None),
                answer_chars=len(answer), group=self._social.is_group(message), address_reason=address_reason,
                reaction=reaction or "", reply_to_message_id=int(message_id) if message_id else None,
                approval=bool(approval),
            )
        except Exception as exc:
            typing_stop.set()
            problem("telegram_chat_stream_failed", str(exc), telegram_user_id=user_id)
            if telegram_message_id:
                self._edit_stream_message(chat_id, telegram_message_id, _partial_text(answer + "\n\nОшибка: " + str(exc)))
            else:
                self._send(chat_id, f"Не удалось получить ответ Лешего: {exc}")

    def _handle_callback_query(self, query: dict) -> None:
        callback_id = str(query.get("id") or "")
        sender = query.get("from") or {}
        user_id = str(sender.get("id") or "")
        data = str(query.get("data") or "")
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        tg_message_id = message.get("message_id")
        if not data.startswith("osdel:"):
            if callback_id:
                try: self._api("answerCallbackQuery", {"callback_query_id": callback_id}, timeout=5)
                except Exception: pass
            return
        if not self._is_owner(user_id):
            if callback_id:
                self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Подтвердить удаление может только хозяин Лешего.", "show_alert": True}, timeout=5)
            return
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        allow = parts[1] == "y"
        approval_id = parts[2]
        controller = getattr(self.app, "os_control", None)
        if controller is None or not callable(getattr(controller, "resolve", None)):
            result = {"message": "Модуль подтверждения системных действий недоступен."}
        else:
            result = controller.resolve(approval_id, owner_id=f"telegram:{user_id}", allow=allow)
        message_text = str(result.get("message") or ("Действие выполнено." if allow else "Действие отменено."))
        if callback_id:
            try: self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": message_text[:180]}, timeout=8)
            except Exception: pass
        if chat_id is not None and tg_message_id:
            old = str(message.get("text") or message.get("caption") or "").strip()
            self._edit_stream_message(chat_id, int(tg_message_id), _partial_text(old + "\n\n" + message_text), reply_markup={"inline_keyboard": []})
        event("telegram_os_approval_resolved", telegram_user_id=user_id, allowed=allow, approval_id=approval_id, status=str(result.get("status") or ""))

    def _run(self) -> None:
        backoff = 0.5
        if self._bot_id is None:
            self._refresh_bot_identity()
        while not self._stop.is_set():
            try:
                query = parse.urlencode({"timeout": 10, "offset": self._offset, "allowed_updates": json.dumps(["message", "callback_query"])})
                result = self._api("getUpdates?" + query, timeout=15) or []
                queued = 0
                for update in result:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    callback = update.get("callback_query")
                    if isinstance(callback, dict):
                        try:
                            self._handle_callback_query(callback)
                        except Exception as exc:
                            problem("telegram_callback_failed", str(exc))
                    message = update.get("message")
                    if isinstance(message, dict):
                        try:
                            self._queue.put_nowait(message); queued += 1
                        except queue.Full:
                            problem("telegram_queue_full", "Очередь Telegram переполнена")
                if queued:
                    self._queue.join()
                self._last_error = ""
                backoff = 0.5
            except (error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                self._last_error = str(exc)[:500]
                problem("telegram_poll_failed", self._last_error, retry_seconds=backoff)
                self._stop.wait(backoff); backoff = min(backoff * 2, 5.0)
            except Exception as exc:
                self._last_error = str(exc)[:500]
                problem("telegram_unexpected_error", self._last_error)
                self._stop.wait(1.0)


__all__ = ["FastTelegramBridge"]
