from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


DEFAULT_VOICE_GENDER = "male"
_ALLOWED_GENDERS = {"male", "female"}


def normalize_voice_gender(value: str | None) -> str:
    """Return a supported voice gender, defaulting to Leshy's male voice."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _ALLOWED_GENDERS else DEFAULT_VOICE_GENDER


@dataclass(frozen=True)
class VoicePreference:
    """Shared cross-interface voice preference."""

    selected_gender: str = DEFAULT_VOICE_GENDER

    @property
    def effective_gender(self) -> str:
        return normalize_voice_gender(self.selected_gender)


class VoicePreferenceStore:
    """Persistent voice preference stored in LeshyBrain.

    The saved user choice has priority over the application default. A software
    update or restart must never silently reset it.
    """

    def __init__(self, brain_dir: Path | str):
        self.path = Path(brain_dir) / "Settings" / "voice.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> VoicePreference:
        if not self.path.exists():
            return VoicePreference(DEFAULT_VOICE_GENDER)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return VoicePreference(DEFAULT_VOICE_GENDER)
        if not isinstance(raw, dict):
            return VoicePreference(DEFAULT_VOICE_GENDER)
        return VoicePreference(normalize_voice_gender(raw.get("selected_gender")))

    def save(self, selected_gender: str) -> VoicePreference:
        preference = VoicePreference(normalize_voice_gender(selected_gender))
        payload = {
            "schema": 1,
            "selected_gender": preference.effective_gender,
        }
        fd, temp_name = tempfile.mkstemp(prefix="voice.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return preference

    def effective_gender(self) -> str:
        return self.load().effective_gender
