from __future__ import annotations

import os
from pathlib import Path

from .secret_store import load_secret, save_secret


class HuggingFaceSettings:
    """Защищённое хранение HF token и экспорт в стандартное окружение Hub."""

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.path = self.brain_dir / "secrets" / "huggingface-token.secret"

    def token(self) -> str:
        try:
            return load_secret(self.path)
        except Exception:
            return ""

    def save(self, token: str) -> None:
        value = str(token or "").strip()
        if value:
            save_secret(self.path, value)
            os.environ["HF_TOKEN"] = value
        else:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            os.environ.pop("HF_TOKEN", None)

    def apply(self) -> bool:
        value = self.token()
        if value:
            os.environ["HF_TOKEN"] = value
            return True
        return False

    def status(self) -> dict:
        return {
            "token_saved": bool(self.token()),
            "token_exposed": False,
            "environment_ready": bool(os.getenv("HF_TOKEN", "").strip()),
            "xet_enabled": self._xet_available(),
            "anonymous_downloads_allowed": True,
        }

    @staticmethod
    def _xet_available() -> bool:
        try:
            import hf_xet  # noqa: F401
            return True
        except Exception:
            return False


__all__ = ["HuggingFaceSettings"]
