"""Lossless decoding of web pages, including legacy Russian Windows-1251.

Never use errors='replace' on web material that may become a knowledge claim.
"""
from __future__ import annotations

import codecs
import re

from .text_integrity import corruption_reason

_CHARSET = re.compile(r"\bcharset\s*=\s*['\"]?\s*([a-zA-Z0-9._-]+)", re.I)
_META = re.compile(rb"<meta\b[^>]{0,1024}>", re.I)


def _declared_charsets(payload: bytes, content_type: str, html: bool) -> list[str]:
    found = []
    header = _CHARSET.search(content_type or "")
    if header:
        found.append(header.group(1))
    if html:
        for tag in _META.findall(payload[:16_384]):
            match = _CHARSET.search(tag.decode("ascii", errors="ignore"))
            if match:
                found.append(match.group(1))
                break
    return found


def _legacy_cyrillic_plausible(text: str) -> bool:
    letters = sum(character.isalpha() for character in text)
    cyrillic = sum("\u0400" <= character <= "\u052f" for character in text)
    return cyrillic >= 8 and cyrillic * 3 >= max(letters, 1)


def decode_web_text(payload: bytes, content_type: str = "", *, html: bool = False) -> str:
    """Decode only losslessly and reject replacement glyphs and mojibake.

    With an absent charset, legacy Cyrillic fallback is only accepted when
    the resulting text has enough Cyrillic characters to be plausible.
    """
    if not isinstance(payload, bytes):
        raise TypeError("web payload must be bytes")
    if not payload:
        return ""
    # A valid UTF-8 byte stream must never be reinterpreted as KOI8-R/CP1251
    # just to hide a replacement glyph already present in the source.
    if not (payload.startswith(codecs.BOM_UTF16_LE) or payload.startswith(codecs.BOM_UTF16_BE)):
        try:
            utf8_text = payload.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            pass
        else:
            if corruption_reason(utf8_text):
                raise UnicodeError("Источник содержит повреждённый Unicode; обучение отклонено")
            return utf8_text

    declared = _declared_charsets(payload, content_type, html)
    candidates: list[tuple[str, bool]] = []
    if payload.startswith(codecs.BOM_UTF8):
        candidates.append(("utf-8-sig", True))
    if payload.startswith(codecs.BOM_UTF16_LE) or payload.startswith(codecs.BOM_UTF16_BE):
        candidates.append(("utf-16", True))
    candidates.extend((encoding, True) for encoding in declared)
    candidates.append(("utf-8-sig", True))
    # An undeclared KOI8-R guess can turn damaged bytes into convincing nonsense.
    # KOI8-R remains supported when explicitly announced by HTTP/meta charset.
    candidates.append(("cp1251", False))
    tried = set()
    for encoding, declared_or_utf8 in candidates:
        if encoding.lower() in tried:
            continue
        tried.add(encoding.lower())
        try:
            text = payload.decode(encoding, errors="strict")
        except (LookupError, UnicodeError, ValueError):
            continue
        if corruption_reason(text):
            continue
        if not declared_or_utf8 and not _legacy_cyrillic_plausible(text):
            continue
        return text
    raise UnicodeError("Страница содержит повреждённый текст или неизвестную кодировку; обучение отклонено")
