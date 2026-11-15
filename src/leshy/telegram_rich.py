from __future__ import annotations

import html
import re
from typing import Iterable

from .telegram import TelegramBridge as BaseTelegramBridge


_FENCE_RE = re.compile(r"```\s*([A-Za-z0-9_+#.\-]*)\s*\n([\s\S]*?)```", re.MULTILINE)
_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    value = _THINK_RE.sub("", str(text or ""))
    lower = value.lower()
    open_index = lower.find("<think>")
    if open_index >= 0:
        value = value[:open_index]
    return value.replace("</think>", "").strip()


def _plain_html(text: str) -> str:
    return html.escape(str(text or ""), quote=False)


def _code_html(language: str, code: str) -> str:
    lang = re.sub(r"[^A-Za-z0-9_+#.\-]", "", language or "")[:40]
    escaped = html.escape(str(code or ""), quote=False)
    if lang:
        return f'<pre><code class="language-{html.escape(lang, quote=True)}">{escaped}</code></pre>'
    return f"<pre>{escaped}</pre>"


def telegram_html_chunks(text: str, *, max_chars: int = 3900) -> list[str]:
    """Преобразует fenced code в Telegram HTML и режет ответ без разрыва тегов."""
    value = _strip_think(text)
    parts: list[tuple[str, str, str]] = []
    offset = 0
    for match in _FENCE_RE.finditer(value):
        if match.start() > offset:
            parts.append(("text", "", value[offset:match.start()]))
        parts.append(("code", match.group(1) or "", match.group(2) or ""))
        offset = match.end()
    if offset < len(value):
        parts.append(("text", "", value[offset:]))
    if not parts:
        parts = [("text", "", value)]

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for kind, language, raw in parts:
        if kind == "code":
            # Очень длинный код делим на несколько самостоятельных pre-блоков.
            while raw:
                take = raw[:3000]
                raw = raw[3000:]
                rendered = _code_html(language, take)
                if current and len(current) + len(rendered) + 1 > max_chars:
                    flush()
                if len(rendered) > max_chars:
                    # Дополнительная страховка для необычно длинных HTML entity.
                    step = 1800
                    for i in range(0, len(take), step):
                        piece = _code_html(language, take[i:i + step])
                        if current:
                            flush()
                        chunks.append(piece)
                else:
                    current = (current + "\n" + rendered).strip() if current else rendered
            continue

        raw_text = raw
        while raw_text:
            room = max(200, max_chars - len(current) - 1)
            candidate = raw_text[:room]
            if len(raw_text) > room:
                split_at = max(candidate.rfind("\n"), candidate.rfind(" "))
                if split_at > room // 2:
                    candidate = candidate[:split_at + 1]
            rendered = _plain_html(candidate)
            if current and len(current) + len(rendered) + 1 > max_chars:
                flush()
                continue
            current = (current + "\n" + rendered).strip() if current else rendered
            raw_text = raw_text[len(candidate):]
            if raw_text and len(current) >= max_chars - 200:
                flush()
    flush()
    return chunks or ["…"]


class TelegramBridge(BaseTelegramBridge):
    """Telegram bridge с форматированием кода как настоящих pre/code блоков."""

    def _send_answer(self, chat_id: int | str, text: str, *, reply_to_message_id: int | None = None) -> None:
        chunks = telegram_html_chunks(text)
        for index, rendered in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": rendered or "…", "parse_mode": "HTML"}
            # Каждый первый ответ остаётся привязанным к исходному сообщению.
            if index == 0 and reply_to_message_id:
                payload["reply_parameters"] = {
                    "message_id": int(reply_to_message_id),
                    "allow_sending_without_reply": True,
                }
            self._api("sendMessage", payload, timeout=10)


__all__ = ["TelegramBridge", "telegram_html_chunks"]
