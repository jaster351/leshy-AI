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
from .telegram_social import TelegramSocialPolicy
from .telegram_proxy import TelegramProxyTransport


_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)
_ADMIN_COMMANDS = {"/learning_on", "/learning_off"}
_MAX_TELEGRAM_VOICE_BYTES = 20 * 1024 * 1024
_MAX_TELEGRAM_VOICE_SECONDS = 5 * 60


def _telegram_answer_html(text: str) -> str:
    """Возвращает безопасный HTML-ответ Telegram без необработанного reasoning."""
    value = str(text or "")
    parts: list[str] = []
    offset = 0
    for match in _THINK_RE.finditer(value):
        visible = value[offset:match.start()]
        if visible:
            parts.append(html.escape(visible))
        offset = match.end()
    tail = value[offset:]
    lower_tail = tail.lower()
    open_index = lower_tail.find("<think>")
    if open_index >= 0:
        tail = tail[:open_index]
    if tail:
        parts.append(html.escape(tail.replace("</think>", "")))
    return "".join(parts).strip()


def _telegram_message_text(message: dict) -> str:
    """Возвращает полезное содержимое Telegram-сообщения для reply-контекста."""
    text = str(message.get("text") or message.get("caption") or "").strip()
    if text:
        return text
    media: list[str] = []
    if message.get("photo"):
        media.append("фото/изображение")
    if message.get("document"):
        document = message.get("document") or {}
        name = str(document.get("file_name") or "").strip()
        media.append(f"документ {name}" if name else "документ")
    if message.get("video"):
        media.append("видео")
    if message.get("animation"):
        media.append("анимация")
    if message.get("audio"):
        media.append("аудио")
    if message.get("voice"):
        media.append("голосовое сообщение")
    if message.get("sticker"):
        media.append("стикер")
    if message.get("location"):
        media.append("геопозиция")
    return "[" + ", ".join(media) + "]" if media else "[сообщение без текста]"


def _telegram_sender_label(message: dict) -> str:
    sender = message.get("from") or {}
    first = str(sender.get("first_name") or "").strip()
    last = str(sender.get("last_name") or "").strip()
    username = str(sender.get("username") or "").strip()
    name = " ".join(part for part in (first, last) if part).strip()
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    if name:
        return name
    sender_id = str(sender.get("id") or "").strip()
    return f"Telegram user {sender_id}" if sender_id else "неизвестный автор"


def _telegram_reply_prompt(message: dict, text: str) -> tuple[str, int | None]:
    """Добавляет выбранное через Telegram Reply сообщение как приоритетный контекст.

    В истории после исходной реплики могут уже находиться новые сообщения. Поэтому
    сам факт reply должен иметь больший семантический приоритет, чем принцип
    «последнее сообщение в чате». Служебный контекст не меняет Telegram UI, но
    делает цель ответа однозначной для модели.
    """
    replied = message.get("reply_to_message")
    if not isinstance(replied, dict):
        return text, None
    target_id_raw = replied.get("message_id")
    try:
        target_id = int(target_id_raw) if target_id_raw is not None else None
    except (TypeError, ValueError):
        target_id = None
    target = _telegram_message_text(replied)[:6000]
    author = _telegram_sender_label(replied)[:300]
    quote = message.get("quote")
    quote_text = ""
    if isinstance(quote, dict):
        quote_text = str(quote.get("text") or "").strip()[:3000]

    parts = [
        "[Контекст Telegram Reply — приоритетный]",
        "Пользователь нажал «Ответить» на конкретное сообщение ниже. Текущий запрос относится прежде всего к нему. "
        "Даже если после этого сообщения в истории чата уже появились другие реплики, не подменяй цель ответа последним сообщением.",
        f"ID выбранного сообщения: {target_id if target_id is not None else 'неизвестен'}",
        f"Автор выбранного сообщения: {author}",
        "Содержимое выбранного сообщения:",
        target,
    ]
    if quote_text:
        parts.extend(("Выделенная пользователем цитата:", quote_text))
    parts.extend(("[Текущий текст пользователя]", text))
    return "\n".join(parts), target_id


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
        # Ошибку расшифровки одного и того же DPAPI-файла не нужно
        # повторять при каждом запросе статуса из нескольких вкладок.
        self._token_load_error_key: tuple[str, int, int] | None = None
        self._offset = 0
        self._committed_offset = 0
        self._cluster_vote_error = ""
        self._social = TelegramSocialPolicy()
        self._bot_id: int | None = None
        self._bot_username = ""
        self.proxy = TelegramProxyTransport(self.brain_dir)

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
            try:
                stat = self.token_path.stat()
                fingerprint = (type(exc).__name__ + ": " + str(exc), stat.st_size, stat.st_mtime_ns)
            except OSError:
                fingerprint = (type(exc).__name__ + ": " + str(exc), -1, -1)
            if fingerprint != self._token_load_error_key:
                self._token_load_error_key = fingerprint
                detail = str(exc)
                if "CryptUnprotectData failed" in detail:
                    detail += (
                        "; Telegram-токен DPAPI привязан к исходному Windows-профилю. "
                        "После переноса LeshyBrain настройте токен заново на этом ПК; "
                        "не копируйте и не расшифровывайте секрет на другом узле."
                    )
                problem("telegram_secret_load_failed", detail)
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
        self._bot_id = None
        self._bot_username = ""
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
            "proxy": self.proxy.status(),
            "allowed_users": len(self._allowed()),
            "allow_all": bool(access["allow_all"]),
            "owner_id": str(access["owner_id"]),
            "owner_configured": bool(access["owner_id"]),
            "running": bool(self._thread and self._thread.is_alive()),
            "worker_running": bool(self._worker_thread and self._worker_thread.is_alive()),
            "queue_size": self._queue.qsize(),
            "last_error": self._last_error,
            "bot_username": self._bot_username,
            "group_context_aware": True,
            "natural_name_addressing": True,
            "message_reactions": True,
            "reply_to_each_message": True,
            "reply_target_priority": True,
            "backlog_before_ack": True,
            "cluster_telegram_election": (
                getattr(self.app, "distributed_cluster", None).telegram_election_status()
                if callable(getattr(getattr(self.app, "distributed_cluster", None), "telegram_election_status", None))
                else {}
            ),
            "privacy_mode_note": "Для чтения обычных сообщений группы без @упоминания Privacy Mode бота должен быть отключён в Telegram.",
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
        with self.proxy.open(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description") or "Telegram API error"))
        return body.get("result")

    def _download_telegram_voice(self, voice: dict) -> bytes:
        """Скачивает голосовое через официальный Bot API с ограничением размера.

        Telegram возвращает file_path только после getFile. Токен бота и URL
        никогда не включаются в исключения или диагностические сообщения.
        """
        if not isinstance(voice, dict):
            raise ValueError("Некорректное голосовое сообщение")
        file_id = str(voice.get("file_id") or "").strip()
        if not file_id:
            raise ValueError("В голосовом сообщении отсутствует file_id")
        try:
            duration = int(voice.get("duration") or 0)
            size = int(voice.get("file_size") or 0)
        except (ValueError, TypeError) as exc:
            raise ValueError("Некорректные параметры голосового сообщения") from exc
        if duration > _MAX_TELEGRAM_VOICE_SECONDS:
            raise ValueError("Голосовое сообщение длиннее пяти минут")
        if size > _MAX_TELEGRAM_VOICE_BYTES:
            raise ValueError("Голосовое сообщение превышает 20 МБ")

        metadata = self._api("getFile", {"file_id": file_id}, timeout=15)
        if not isinstance(metadata, dict):
            raise RuntimeError("Telegram не вернул сведения о голосовом файле")
        try:
            remote_size = int(metadata.get("file_size") or 0)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Telegram вернул некорректный размер файла") from exc
        if remote_size > _MAX_TELEGRAM_VOICE_BYTES:
            raise ValueError("Голосовое сообщение превышает 20 МБ")

        file_path = str(metadata.get("file_path") or "")
        path_parts = file_path.split("/")
        if (
            not file_path or file_path.startswith("/") or "\\" in file_path
            or any(part in ("", ".", "..") for part in path_parts)
            or parse.urlsplit(file_path).scheme
        ):
            raise RuntimeError("Telegram вернул некорректный путь к голосовому файлу")
        token = self._token()
        if not token:
            raise RuntimeError("Telegram token не настроен")
        url = f"https://api.telegram.org/file/bot{token}/{parse.quote(file_path, safe='/')}"
        try:
            with self.proxy.open(request.Request(url, method="GET"), timeout=30) as response:
                length = response.headers.get("Content-Length") if response.headers else None
                if length and str(length).isdigit() and int(length) > _MAX_TELEGRAM_VOICE_BYTES:
                    raise ValueError("Голосовое сообщение превышает 20 МБ")
                audio = response.read(_MAX_TELEGRAM_VOICE_BYTES + 1)
        except error.HTTPError as exc:
            raise RuntimeError(f"Telegram не отдал голосовой файл (HTTP {exc.code})") from None
        except (error.URLError, OSError, TimeoutError):
            raise RuntimeError("Ошибка скачивания голосового файла из Telegram") from None
        if not audio:
            raise ValueError("Telegram вернул пустой голосовой файл")
        if len(audio) > _MAX_TELEGRAM_VOICE_BYTES:
            raise ValueError("Голосовое сообщение превышает 20 МБ")
        return audio

    def _resolve_message_text(self, message: dict, *, chat_id: int | str, user_id: str) -> str | None:
        """Преобразует голосовое в текст уже ПОСЛЕ проверки доступа и адресации.

        None означает ошибку, о которой пользователь уже уведомлён. Для
        Telegram и Web используется один ASR-экземпляр с общим request gate.
        """
        caption = str(message.get("text") or message.get("caption") or "").strip()
        voice = message.get("voice")
        if voice is None:
            return caption
        try:
            audio = self._download_telegram_voice(voice)
            asr = getattr(self.app, "_voice_asr", None)
            if asr is None:
                from .voice_asr import LocalVoiceASR

                asr = LocalVoiceASR(self.brain_dir)
                self.app._voice_asr = asr
            transcript = str(asr.transcribe(audio, content_type="audio/ogg").text or "").strip()
        except Exception as exc:
            # Не записываем в логи текст ошибки: библиотека HTTP может
            # включать в исключения секретный URL с Telegram bot token.
            problem("telegram_voice_failed", type(exc).__name__, telegram_user_id=user_id)
            self._send(chat_id, "Не удалось распознать голосовое сообщение. Попробуйте ещё раз или напишите текст.")
            return None
        if not transcript:
            self._send(chat_id, "В голосовом сообщении не удалось разобрать речь. Попробуйте ещё раз.")
            return None
        event("telegram_voice_transcribed", telegram_user_id=user_id, chars=len(transcript))
        return (caption + "\n" + transcript).strip() if caption else transcript

    def _refresh_bot_identity(self) -> None:
        try:
            me = self._api("getMe", timeout=10) or {}
            self._bot_id = int(me.get("id") or 0) or None
            self._bot_username = str(me.get("username") or "").strip()
            event("telegram_identity_loaded", bot_id=self._bot_id, bot_username=self._bot_username)
        except Exception as exc:
            problem("telegram_identity_failed", str(exc))

    def _send(self, chat_id: int | str, text: str) -> None:
        self._api("sendMessage", {"chat_id": chat_id, "text": text[:4000]}, timeout=10)

    def _send_answer(self, chat_id: int | str, text: str, *, reply_to_message_id: int | None = None) -> None:
        rendered = _telegram_answer_html(text[:3800])
        payload = {"chat_id": chat_id, "text": rendered or "…", "parse_mode": "HTML"}
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": int(reply_to_message_id), "allow_sending_without_reply": True}
        self._api("sendMessage", payload, timeout=10)

    def _set_reaction(self, chat_id: int | str, message_id: int | None, emoji: str | None) -> None:
        if not message_id or not emoji:
            return
        try:
            self._api(
                "setMessageReaction",
                {
                    "chat_id": chat_id,
                    "message_id": int(message_id),
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                    "is_big": False,
                },
                timeout=8,
            )
            event("telegram_reaction_set", chat_id=str(chat_id), message_id=int(message_id), emoji=emoji)
        except Exception as exc:
            problem("telegram_reaction_failed", str(exc), chat_id=str(chat_id), message_id=int(message_id))

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
        text = str(message.get("text") or message.get("caption") or "").strip()
        chat_id = chat.get("id")
        user_id = str(sender.get("id") or "")
        message_id = message.get("message_id")
        if (not text and message.get("voice") is None) or chat_id is None or not user_id:
            return

        addressed, address_reason = self._social.addressed_to_leshy(
            message,
            bot_id=self._bot_id,
            bot_username=self._bot_username,
        )
        if self._social.is_group(message) and not addressed:
            event(
                "telegram_group_message_ignored",
                chat_id=str(chat_id),
                telegram_user_id=user_id,
                reason=address_reason,
            )
            return

        if not self._can_chat(user_id):
            problem("telegram_access_denied", "Telegram ID не разрешён", telegram_user_id=user_id)
            if not self._social.is_group(message):
                self._send(chat_id, "Доступ к Лешему не настроен для этого Telegram ID.")
            return

        text = self._resolve_message_text(message, chat_id=chat_id, user_id=user_id)
        if not text:
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

        chat_text, reply_target_id = _telegram_reply_prompt(message, text)
        if reply_target_id is not None:
            event(
                "telegram_reply_context_applied",
                chat_id=str(chat_id),
                telegram_user_id=user_id,
                message_id=int(message_id) if message_id else None,
                target_message_id=reply_target_id,
            )

        self._send_typing(chat_id)
        typing_stop = threading.Event()

        def typing_loop() -> None:
            while not typing_stop.wait(4.0):
                self._send_typing(chat_id)

        typing_thread = threading.Thread(target=typing_loop, name="leshy-telegram-typing", daemon=True)
        typing_thread.start()
        try:
            result = self.app.chat(
                chat_text,
                owner_id=f"telegram:{user_id}",
                session_id=f"telegram:{chat_id}:{user_id}",
            )
            answer = str(result["answer"])
            self._send_answer(
                chat_id,
                answer,
                reply_to_message_id=int(message_id) if message_id else None,
            )
            self._social.mark_leshy_answered(message)
            reaction = self._social.reaction_for(message, addressed=addressed, answer=answer)
            self._set_reaction(chat_id, int(message_id) if message_id else None, reaction)
            event(
                "telegram_message_answered",
                chat_id=str(chat_id),
                telegram_user_id=user_id,
                group=self._social.is_group(message),
                address_reason=address_reason,
                reaction=reaction or "",
                reply_to_message_id=int(message_id) if message_id else None,
                reply_target_message_id=reply_target_id,
            )
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

    def _cluster_can_poll(self) -> bool:
        """Only the voted owner calls Telegram getUpdates; every Web stays online."""
        cluster = getattr(self.app, "distributed_cluster", None)
        decision = getattr(cluster, "telegram_poll_decision", None)
        if not callable(decision):
            return True
        try:
            token = self._token()
            if not token:
                return False
            allowed, acknowledged = decision(token, acknowledged_offset=self._committed_offset)
            # The former leader advertised only updates processed by its worker.
            self._offset = max(self._offset, int(acknowledged))
            self._committed_offset = max(self._committed_offset, int(acknowledged))
            self._cluster_vote_error = ""
            return bool(allowed)
        except Exception as exc:
            # Fail closed rather than starting a competing poller on a broken vote.
            reason = type(exc).__name__
            if reason != self._cluster_vote_error:
                self._cluster_vote_error = reason
                problem("telegram_cluster_vote_failed", reason)
            return False

    def _run(self) -> None:
        backoff = 0.5
        conflict_backoff = 30.0  # Другой экземпляр уже опрашивает этот bot token.
        if self._bot_id is None:
            self._refresh_bot_identity()
        while not self._stop.is_set():
            if not self._cluster_can_poll():
                self._stop.wait(1.0)
                continue
            try:
                query = parse.urlencode(
                    {"timeout": 10, "offset": self._offset, "allowed_updates": json.dumps(["message"])}
                )
                result = self._api("getUpdates?" + query, timeout=15) or []
                queued = 0
                for update in result:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    message = update.get("message")
                    if isinstance(message, dict):
                        try:
                            self._queue.put_nowait(message)
                            queued += 1
                        except queue.Full:
                            problem("telegram_queue_full", "Очередь Telegram переполнена")
                # Не подтверждаем следующему getUpdates обработку этой пачки, пока
                # worker не закончил каждое сообщение. Это сохраняет backlog при
                # аварийном выключении между получением и ответом.
                if queued:
                    self._queue.join()
                self._committed_offset = max(self._committed_offset, self._offset)
                self._last_error = ""
                backoff = 0.5
                conflict_backoff = 30.0
            except (error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                self._last_error = str(exc)[:500]
                # HTTP 409 означает конкурирующий getUpdates того же токена.
                # Не устраиваем бесконечную гонку каждые 5 секунд: уступаем
                # другому poller и пробуем снова позже, не теряя offset.
                conflict = "HTTP 409" in self._last_error and "Conflict" in self._last_error
                retry = conflict_backoff if conflict else backoff
                problem("telegram_poll_failed", self._last_error, retry_seconds=retry)
                self._stop.wait(retry)
                if conflict:
                    conflict_backoff = min(conflict_backoff * 2, 300.0)
                else:
                    backoff = min(backoff * 2, 5.0)
            except Exception as exc:  # pragma: no cover
                self._last_error = str(exc)[:500]
                problem("telegram_unexpected_error", self._last_error)
                self._stop.wait(1.0)
