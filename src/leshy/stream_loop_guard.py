"""Bounded, incremental detection of repeated generated prose."""
from __future__ import annotations

import re


class StreamLoopGuard:
    """Pass through text until the model repeats a substantial block three times.

    Kept state is bounded; short legitimate repetitions are deliberately ignored.
    The guard never requests additional model output and does not edit the prompt.
    """

    _TAIL_CHARS = 4200
    _MAX_PERIOD = 1024
    _MIN_PERIOD = 40
    _ANCHOR = 24

    def __init__(self) -> None:
        self._tail = ""
        self.triggered = False

    @classmethod
    def _repeated_suffix(cls, text: str) -> bool:
        text = " ".join(text.casefold().split())[-cls._TAIL_CHARS:]
        end = len(text)
        if end < cls._MIN_PERIOD * 3:
            return False
        suffix = text[-cls._ANCHOR:]
        start = max(0, end - cls._MAX_PERIOD - cls._ANCHOR)
        pos = text.rfind(suffix, start, end - cls._ANCHOR)
        while pos >= start:
            period = end - cls._ANCHOR - pos
            if cls._MIN_PERIOD <= period <= cls._MAX_PERIOD and end >= period * 3:
                last = text[end - period:end]
                if (last == text[end - period * 2:end - period]
                        and last == text[end - period * 3:end - period * 2]):
                    words = re.findall(r"[^\W\d_]+", last, flags=re.UNICODE)
                    # Discard partially cut first/last words. This avoids treating
                    # rotated two-word refrains as long varied dialogue.
                    if len(words) >= 6 and len(set(words[1:-1])) >= 4:
                        return True
            pos = text.rfind(suffix, start, pos)
        return False

    def push(self, delta: str) -> str:
        """Return safe prefix of delta, stopping before a detected loop."""
        if self.triggered or not delta:
            return ""
        accepted: list[str] = []
        for offset in range(0, len(delta), 48):
            piece = delta[offset:offset + 48]
            candidate = (self._tail + piece)[-self._TAIL_CHARS:]
            if self._repeated_suffix(candidate):
                self.triggered = True
                break
            self._tail = candidate
            accepted.append(piece)
        return "".join(accepted)
