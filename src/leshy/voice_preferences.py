from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile


DEFAULT_VOICE_GENDER = "male"
DEFAULT_VOICE_ENGINE = "auto"
DEFAULT_VOICE_PROFILE = "natural"
_ALLOWED_GENDERS = {"male", "female"}
_ALLOWED_ENGINES = {"auto", "windows", "piper", "rhvoice", "espeak"}
_ALLOWED_PROFILES = {"natural", "heroic", "noir", "android", "villain", "narrator"}


def normalize_voice_gender(value: str | None) -> str:
    """Return a supported voice gender, defaulting to Leshy's male voice."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _ALLOWED_GENDERS else DEFAULT_VOICE_GENDER


def normalize_voice_engine(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _ALLOWED_ENGINES else DEFAULT_VOICE_ENGINE


def normalize_voice_profile(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _ALLOWED_PROFILES else DEFAULT_VOICE_PROFILE


def normalize_rate(value: object) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.70, min(1.30, rate))


@dataclass(frozen=True)
class VoicePreference:
    """Shared cross-interface voice preference.

    `selected_gender` remains the first field for backwards compatibility with
    older code/tests that construct VoicePreference("female").
    """

    selected_gender: str = DEFAULT_VOICE_GENDER
    engine: str = DEFAULT_VOICE_ENGINE
    voice_id: str = ""
    profile: str = DEFAULT_VOICE_PROFILE
    rate: float = 1.0

    @property
    def effective_gender(self) -> str:
        return normalize_voice_gender(self.selected_gender)

    @property
    def effective_engine(self) -> str:
        return normalize_voice_engine(self.engine)

    def as_dict(self) -> dict:
        value = asdict(self)
        value.update({
            "selected_gender": self.effective_gender,
            "engine": self.effective_engine,
            "profile": normalize_voice_profile(self.profile),
            "rate": normalize_rate(self.rate),
        })
        return value


class VoicePreferenceStore:
    """Persistent voice preference stored in LeshyBrain/Settings/voice.json.

    Schema 2 is backwards compatible with schema 1 (gender only). A software
    update or restart must never silently reset the user's voice choice.
    """

    def __init__(self, brain_dir: Path | str):
        self.path = Path(brain_dir) / "Settings" / "voice.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> VoicePreference:
        if not self.path.exists():
            return VoicePreference()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return VoicePreference()
        if not isinstance(raw, dict):
            return VoicePreference()
        return VoicePreference(
            selected_gender=normalize_voice_gender(raw.get("selected_gender")),
            engine=normalize_voice_engine(raw.get("engine")),
            voice_id=str(raw.get("voice_id") or "").strip()[:200],
            profile=normalize_voice_profile(raw.get("profile")),
            rate=normalize_rate(raw.get("rate", 1.0)),
        )

    def save(
        self,
        selected_gender: str | None = None,
        *,
        engine: str | None = None,
        voice_id: str | None = None,
        profile: str | None = None,
        rate: object | None = None,
    ) -> VoicePreference:
        current = self.load()
        preference = VoicePreference(
            selected_gender=normalize_voice_gender(selected_gender if selected_gender is not None else current.selected_gender),
            engine=normalize_voice_engine(engine if engine is not None else current.engine),
            voice_id=str(current.voice_id if voice_id is None else voice_id).strip()[:200],
            profile=normalize_voice_profile(profile if profile is not None else current.profile),
            rate=normalize_rate(current.rate if rate is None else rate),
        )
        payload = {"schema": 2, **preference.as_dict()}
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


__all__ = [
    "DEFAULT_VOICE_GENDER",
    "DEFAULT_VOICE_ENGINE",
    "DEFAULT_VOICE_PROFILE",
    "VoicePreference",
    "VoicePreferenceStore",
    "normalize_voice_gender",
    "normalize_voice_engine",
    "normalize_voice_profile",
    "normalize_rate",
]
