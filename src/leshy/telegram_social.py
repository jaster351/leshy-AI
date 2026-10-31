from __future__ import annotations

import re
import threading
import time
from typing import Any


_LESHEY_NAME_RE = re.compile(
    r"(?iu)(?:^|[\s,.:;!?()\[\]{}\-—])(?:леший|л[её]ша)(?=$|[\s,.:;!?()\[\]{}\-—])"
)
_FOLLOW_UP_RE = re.compile(
    r"(?iu)^\s*(?:а\s+|и\s+|но\s+|тогда\s+|то\s+есть\s+|почему\b|зачем\b|как\b|что\b|где\b|когда\b|"
    r"можешь\b|можно\b|получается\b|понял\b|поняла\b|ясно\b|спасибо\b|нет\b|да\b|верно\b|правильно\b)"
)
_HELPFUL_RE = re.compile(
    r"(?iu)(?:на самом деле|правильно(?:\s+будет|\s+так)?|имеется в виду|объясню|поясню|это потому|"
    r"причина в том|запомни|учти|то есть|другими словами|я имел в виду|я имела в виду)"
)
_POSITIVE_RE = re.compile(
    r"(?iu)(?:спасибо|благодарю|отлично|супер|класс|круто|молодец|получилось|сработало|готово|верно|правильно)"
)
_FUN_RE = re.compile(r"(?iu)(?:ахаха|хаха|лол|смешно|😂|🤣)")
_CELEBRATE_RE = re.compile(r"(?iu)(?:ура\b|победа|заработало|починили|готово!|получилось!)")


class TelegramSocialPolicy:
    """Контекстная адресация и ненавязчивые реакции Лешего в Telegram.

    В группе политика намеренно консервативна: Леший отвечает только когда обращение
    адресовано ему или является коротким продолжением уже начатого с ним диалога.
    Просто упоминание Лешего в разговоре людей не считается приглашением вмешиваться,
    если рядом нет вопроса/обращения к нему.
    """

    ACTIVE_DIALOGUE_SECONDS = 180.0

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def is_group(message: dict[str, Any]) -> bool:
        chat = message.get("chat") or {}
        return str(chat.get("type") or "").lower() in {"group", "supergroup"}

    @staticmethod
    def is_private(message: dict[str, Any]) -> bool:
        chat = message.get("chat") or {}
        return str(chat.get("type") or "").lower() == "private"

    @staticmethod
    def _reply_targets_bot(message: dict[str, Any], bot_id: int | None) -> bool:
        if not bot_id:
            return False
        reply = message.get("reply_to_message") or {}
        author = reply.get("from") or {}
        try:
            return int(author.get("id") or 0) == int(bot_id)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _mentions_username(text: str, username: str) -> bool:
        value = str(username or "").strip().lstrip("@").lower()
        return bool(value and ("@" + value) in text.lower())

    @staticmethod
    def _looks_like_address(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        if "?" in value:
            return True
        lowered = value.lower()
        verbs = (
            "скажи", "подскажи", "ответь", "посмотри", "найди", "сделай", "помоги", "объясни",
            "расскажи", "проверь", "как думаешь", "что думаешь", "что скажешь", "будешь", "можешь",
        )
        return any(token in lowered for token in verbs)

    def _active_follow_up(self, chat_id: str, user_id: str, text: str) -> bool:
        key = (str(chat_id), str(user_id))
        now = time.monotonic()
        with self._lock:
            seen = float(self._active.get(key, 0.0))
            if seen and now - seen > self.ACTIVE_DIALOGUE_SECONDS:
                self._active.pop(key, None)
                seen = 0.0
        return bool(seen and _FOLLOW_UP_RE.search(text))

    def addressed_to_leshy(
        self,
        message: dict[str, Any],
        *,
        bot_id: int | None = None,
        bot_username: str = "",
    ) -> tuple[bool, str]:
        if self.is_private(message):
            return True, "private"
        if not self.is_group(message):
            return False, "unsupported_chat_type"

        text = str(message.get("text") or message.get("caption") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id") or "")
        user_id = str((message.get("from") or {}).get("id") or "")

        if self._reply_targets_bot(message, bot_id):
            return True, "reply_to_leshy"
        if self._mentions_username(text, bot_username):
            return True, "telegram_mention"
        if _LESHEY_NAME_RE.search(text) and self._looks_like_address(text):
            return True, "natural_name"
        if chat_id and user_id and self._active_follow_up(chat_id, user_id, text):
            return True, "dialogue_follow_up"
        return False, "people_conversation"

    def mark_leshy_answered(self, message: dict[str, Any]) -> None:
        if not self.is_group(message):
            return
        chat_id = str((message.get("chat") or {}).get("id") or "")
        user_id = str((message.get("from") or {}).get("id") or "")
        if chat_id and user_id:
            with self._lock:
                self._active[(chat_id, user_id)] = time.monotonic()

    @staticmethod
    def reaction_for(message: dict[str, Any], *, addressed: bool, answer: str = "") -> str | None:
        """Выбирает редкую уместную реакцию вместо механического лайка каждого сообщения."""
        if not addressed:
            return None
        text = str(message.get("text") or message.get("caption") or "").strip()
        if not text:
            return None
        if _FUN_RE.search(text):
            return "😂"
        if _CELEBRATE_RE.search(text):
            return "🎉"
        if _HELPFUL_RE.search(text):
            return "👍"
        if _POSITIVE_RE.search(text) and len(text) <= 500:
            return "👍"
        # Если пользователь дал содержательное пояснение, а ответ Лешего признаёт,
        # что оно помогло/уточнило знание, положительная реакция естественна.
        low_answer = str(answer or "").lower()
        if len(text) >= 80 and any(token in low_answer for token in ("спасибо", "понял", "учту", "уточнение")):
            return "👍"
        return None


__all__ = ["TelegramSocialPolicy"]
