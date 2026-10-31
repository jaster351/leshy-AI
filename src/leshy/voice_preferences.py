from __future__ import annotations

from dataclasses import dataclass


DEFAULT_VOICE_GENDER = "male"
_ALLOWED_GENDERS = {"male", "female"}


def normalize_voice_gender(value: str | None) -> str:
    """Return a supported voice gender, defaulting to Leshy's male voice."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _ALLOWED_GENDERS else DEFAULT_VOICE_GENDER


@dataclass(frozen=True)
class VoicePreference:
    """Shared cross-interface voice preference.

    `selected_gender` is the user's persisted preference. When it is empty or
    invalid, every interface must resolve to the same male default.
    """

    selected_gender: str = DEFAULT_VOICE_GENDER

    @property
    def effective_gender(self) -> str:
        return normalize_voice_gender(self.selected_gender)
