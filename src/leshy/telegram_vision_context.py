from __future__ import annotations

"""Short-lived per-user Telegram photo references.

Store only Telegram file_id/size in process memory (never image bytes or
public records). Prevent "Кто это?" from routing to the text-only persona
when the user has just sent a photo, even if the first vision attempt failed.
"""

from collections import OrderedDict
import re
import threading
import time

from .telegram_vision import image_attachment


_MAX_ENTRIES = 256
_TTL_SECONDS = 10 * 60
_PHOTO_REF_RE = re.compile(
    r"(?iu)(?:"
    r"\b(?:кто|что)\s+(?:это|там|здесь|тут|на\s+(?:фото|фотографии|снимке|изображении))\b"
    r"|\b(?:это\s+кто|это\s+что|а\s+это\s+кто|а\s+это\s+что)\b"
    r"|\b(?:кто|что)\s+(?:за\s+)?(?:зверь|животное|птица|объект|предмет)\b"
    r"|\b(?:опиши|рассмотри|посмотри|разбери)\s+(?:это|фото|фотографию|снимок|изображение)\b"
    r"|\b(?:на\s+)?(?:этом\s+)?(?:фото|фотографии|снимке|изображении)\b"
    r"|\b(?:что\s+(?:ты\s+)?видишь|кого\s+видишь|кого\s+я\s+сфотографировал)\b"
    r"|\b(?:где\s+(?:это|снято|сделано|находится|сфотографировано)|что\s+(?:это\s+)?за\s+(?:место|город|страна|улица)|(?:в\s+каком|в\s+какой)\s+(?:городе|стране|регионе)|(?:определи|угадай|найди)\s+(?:местоположение|геолокацию|город|страну)|where\s+(?:is|was)\s+this)\b"
    r")"
)


def refers_to_recent_photo(text: str) -> bool:
    value = " ".join(str(text or "").lower().split())
    if len(value) > 500 or value.startswith("/"):
        return False
    return bool(_PHOTO_REF_RE.search(value))


class RecentTelegramPhotos:
    """Recent photo pointer keyed to Telegram chat and sender, with TTL."""

    def __init__(self, *, ttl_seconds: int = _TTL_SECONDS, limit: int = _MAX_ENTRIES):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.limit = max(1, int(limit))
        self._lock = threading.RLock()
        self._entries: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
        # Ephemeral photo context only. Persisted lessons contain NO image bytes.
        self._geo: dict[tuple[str, str], dict] = {}

    def remember(self, chat_id: int | str, sender_id: str, message: dict) -> bool:
        attachment = image_attachment(message)
        if attachment is None or chat_id is None or not sender_id:
            return False
        # Keep no Telegram request URL, image contents, caption or bot tokens.
        selected = {
            "file_id": str(attachment["file_id"]),
            "file_size": attachment.get("file_size", 0),
        }
        kind = str(attachment.get("mime_type") or "")
        if kind:
            selected["mime_type"] = kind
        photo = {"photo": [selected]} if "photo" in message else {"document": selected}
        key = (str(chat_id), str(sender_id))
        now = time.monotonic()
        with self._lock:
            self._entries[key] = (now, photo)
            self._geo.pop(key, None)  # A new image replaces the previous reference.
            self._entries.move_to_end(key)
            self._prune(now)
            while len(self._entries) > self.limit:
                removed, _ = self._entries.popitem(last=False)
                self._geo.pop(removed, None)
        return True

    def recall(self, chat_id: int | str, sender_id: str, question: str) -> dict | None:
        if not refers_to_recent_photo(question):
            return None
        now = time.monotonic()
        key = (str(chat_id), str(sender_id))
        with self._lock:
            self._prune(now)
            stored = self._entries.get(key)
            if stored is None:
                return None
            self._entries.move_to_end(key)
            # Give the current question to vision; do not reuse old caption.
            return {**stored[1], "caption": question}

    def record_evidence(
        self, chat_id: int | str, sender_id: str, *,
        image_sha256: str = "", geo_active: bool = False,
    ) -> None:
        """Record only SHA-256 of the image, never its content or metadata."""
        key = (str(chat_id), str(sender_id))
        with self._lock:
            self._prune(time.monotonic())
            if key not in self._entries:
                return
            entry = self._geo.setdefault(key, {})
            if image_sha256:
                entry["digest"] = str(image_sha256)[:64]
            if geo_active:
                entry["geo_active"] = True

    def teaching_context(self, chat_id: int | str, sender_id: str) -> dict | None:
        key = (str(chat_id), str(sender_id))
        with self._lock:
            self._prune(time.monotonic())
            if key not in self._entries:
                return None
            return dict(self._geo.get(key, {}))

    def _prune(self, now: float) -> None:
        expired = [key for key, (seen, _) in self._entries.items()
                   if now - seen > self.ttl_seconds]
        for key in expired:
            self._entries.pop(key, None)
            self._geo.pop(key, None)


__all__ = ["RecentTelegramPhotos", "refers_to_recent_photo"]
