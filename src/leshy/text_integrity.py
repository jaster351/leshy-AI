"""Fail closed on damaged Unicode before adding external material to knowledge.

A replacement glyph (U+FFFD) means that some input bytes were already lost.
Do not guess the original wording: the original source must be read again.
"""
from __future__ import annotations

import re

# Binary/control data must not be promoted to human-readable factual claims.
_INVALID_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_UNPAIRED_SURROGATES = re.compile(r"[\ud800-\udfff]")
# UTF-8 Cyrillic decoded as Windows-1251: e.g. "РџСЂРёРІРµС‚".
_UTF8_AS_CP1251 = re.compile(r"(?:[РС][\u00a0-\u00ff\u0400-\u052f\u2010-\u203a]){3,}")
# UTF-8 decoded as ISO-8859-1/Windows-1252: e.g. "ÐŸÑ€Ð¸".
_UTF8_AS_LATIN1 = re.compile(r"(?:[ÐÑ][\u0080-\u00bf\u0400-\u052f\u2000-\u20ac]){3,}")
_ESCAPED_REPLACEMENT = re.compile(r"(?:\\u[fF]{3}[dD]|&#(?:65533|x[fF]{3}[dD]);)")


def corruption_reason(value: object) -> str | None:
    """Return an actionable reason, or None for usable Unicode text."""
    if not isinstance(value, str):
        return "not_text"
    if "\ufffd" in value or _ESCAPED_REPLACEMENT.search(value):
        return "replacement_character"
    if _UNPAIRED_SURROGATES.search(value):
        return "unpaired_surrogate"
    if _INVALID_CONTROLS.search(value):
        return "invalid_control_character"
    if _UTF8_AS_CP1251.search(value) or _UTF8_AS_LATIN1.search(value):
        return "likely_mojibake"
    return None


def validate_knowledge_text(value: object, *, field: str) -> str:
    reason = corruption_reason(value)
    if reason:
        raise ValueError(f"corrupted knowledge text in {field}: {reason}")
    return value


def corrupted_knowledge_row(row: dict) -> bool:
    """Check existing SQLite rows without touching legitimate, unrelated data."""
    return any(corruption_reason(row.get(field) or "") for field in ("topic", "claim", "source_title"))
