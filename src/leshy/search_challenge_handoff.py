from __future__ import annotations

"""Ephemeral, owner-assisted search challenge handoff.

The Chromium page stays on the originating Playwright thread. Other threads
submit only bounded pointer/keyboard commands through a capability token.
No browser cookies, storage state or credentials cross the handoff API.
"""

import base64
from dataclasses import dataclass, field
import queue
import secrets
import threading
import time
from typing import Callable

_TTL = 600.0
_MAX_ACTIVE = 2
_MAX_TEXT = 256


@dataclass
class ChallengeTicket:
    ticket: str
    engine: str
    url: str
    created: float
    status: str = "pending"
    screenshot: str = ""
    message: str = "Требуется подтверждение в браузере"
    results: list[dict] = field(default_factory=list)
    commands: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=16))
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def public(self) -> dict:
        with self.lock:
            remaining = max(0, int(_TTL - (time.monotonic() - self.created)))
            return {
                "ticket": self.ticket, "engine": self.engine, "url": self.url,
                "status": self.status, "message": self.message,
                "expires_in_seconds": remaining, "screenshot": self.screenshot,
                "results": list(self.results),
            }


class ChallengeBroker:
    def __init__(self):
        self._lock = threading.RLock()
        self._tickets: dict[str, ChallengeTicket] = {}
        self._notifier: Callable[[ChallengeTicket], None] | None = None

    def set_notifier(self, notifier: Callable[[ChallengeTicket], None] | None) -> None:
        with self._lock:
            self._notifier = notifier

    def create(self, page, engine: str) -> ChallengeTicket | None:
        with self._lock:
            self._prune()
            active = sum(t.status == "pending" for t in self._tickets.values())
            if active >= _MAX_ACTIVE:
                return None
            ticket = ChallengeTicket(secrets.token_urlsafe(32), str(engine), str(page.url), time.monotonic())
            self._tickets[ticket.ticket] = ticket
            notify = self._notifier
        self._snapshot(page, ticket)
        if notify:
            try:
                notify(ticket)
            except Exception:
                pass  # A broken notification must not kill the browser session.
        return ticket

    def _prune(self):
        now = time.monotonic()
        for key, item in list(self._tickets.items()):
            if now - item.created > 2 * _TTL:
                self._tickets.pop(key, None)

    @staticmethod
    def _snapshot(page, ticket: ChallengeTicket) -> None:
        try:
            # Only the Playwright thread accesses page and its browser context.
            data = base64.b64encode(page.screenshot(type="png", full_page=False, timeout=4000)).decode("ascii")
        except Exception:
            data = ""
        with ticket.lock:
            ticket.screenshot = "data:image/png;base64," + data if data else ""
            ticket.url = str(page.url or "")[:2000]

    def get(self, token: str) -> dict | None:
        with self._lock:
            self._prune()
            ticket = self._tickets.get(str(token))
        return ticket.public() if ticket else None

    def enqueue(self, token: str, action: str, *, x: float = 0, y: float = 0,
                text: str = "") -> bool:
        with self._lock:
            ticket = self._tickets.get(str(token))
        if ticket is None or ticket.status != "pending":
            return False
        name = str(action).lower()
        if name not in {"click", "type", "press", "scroll", "refresh", "check", "cancel"}:
            return False
        if name == "click" and not (0 <= x <= 1280 and 0 <= y <= 800):
            return False
        if len(text) > _MAX_TEXT or any(c in text for c in ("\x00", "\r")):
            return False
        if name == "press" and text not in {"Enter", "Tab", "Backspace", "Escape", "Space"}:
            return False
        try:
            ticket.commands.put_nowait((name, x, y, text))
            return True
        except queue.Full:
            return False

    @staticmethod
    def _execute(page, name: str, x: float, y: float, text: str) -> None:
        if name == "click":
            page.mouse.click(x, y)
        elif name == "type":
            page.keyboard.insert_text(text)
        elif name == "press":
            page.keyboard.press(text)
        elif name == "scroll":
            page.mouse.wheel(0, max(-800, min(800, y)))
        elif name == "refresh":
            page.reload(wait_until="domcontentloaded", timeout=10000)

    def await_solution(self, page, ticket: ChallengeTicket, ready: Callable[[], bool]) -> bool:
        """Run in the ORIGINAL Playwright thread; never recreate the page."""
        next_capture = 0.0
        while time.monotonic() - ticket.created < _TTL:
            if ready():
                with ticket.lock:
                    ticket.status = "solved"
                    ticket.message = "Проверка пройдена, продолжаю исходный поиск"
                return True
            try:
                name, x, y, value = ticket.commands.get(timeout=0.8)
            except queue.Empty:
                name = "check"
                x = y = 0
                value = ""
            if name == "cancel":
                with ticket.lock:
                    ticket.status = "cancelled"
                    ticket.message = "Ожидание проверки отменено"
                return False
            try:
                self._execute(page, name, x, y, value)
            except Exception:
                with ticket.lock:
                    ticket.message = "Не удалось выполнить действие; обновите снимок"
            now = time.monotonic()
            if now >= next_capture or name != "check":
                self._snapshot(page, ticket)
                next_capture = now + 1.5
        with ticket.lock:
            ticket.status = "expired"
            ticket.message = "Время подтверждения истекло"
            ticket.screenshot = ""
        return False

    @staticmethod
    def complete(ticket: ChallengeTicket, results: list) -> None:
        with ticket.lock:
            ticket.results = [
                {"title": result.title, "url": result.url, "snippet": result.snippet}
                for result in results[:10]
            ]
            ticket.screenshot = ""
            ticket.message = "Исходный поиск продолжен; результаты доступны ниже"


BROKER = ChallengeBroker()

__all__ = ["BROKER", "ChallengeBroker", "ChallengeTicket"]
