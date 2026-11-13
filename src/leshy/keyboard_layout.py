"""Conservative Russian/English keyboard-layout typo detection.

Only suggest a conversion when an entire short natural-language message looks
like text entered using the opposite keyboard layout. Never rewrite code,
commands, URLs, credentials, paths, identifiers or mixed-script messages.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./"
_RU = "ёйцукенгшщзхъфывапролджэячсмитьбю."
_EN_TO_RU = str.maketrans(_EN, _RU)
_RU_TO_EN = str.maketrans(_RU, _EN)
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+", re.UNICODE)
_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_UNSAFE = re.compile(r"[/\\@#{}<>:=_~|$^*+\\d]|https?\\b|www\\.|\\.\\w{2,}", re.IGNORECASE)
_RU_WORDS = frozenset((
    "привет", "пока", "спасибо", "пожалуйста", "да", "нет", "как", "дела",
    "что", "это", "такое", "почему", "где", "когда", "сколько", "можно",
    "нужно", "помоги", "помогите", "сделай", "сделайте", "проверь",
    "проверить", "исправь", "исправить", "покажи", "расскажи", "объясни",
    "напиши", "найди", "хочу", "узнать", "работает", "работает ли",
    "ты", "вы", "он", "она", "оно", "мне", "меня", "тебя", "тебе", "у",
    "в", "на", "с", "по", "для", "не", "и", "или", "ли", "же", "бы",
    "сегодня", "завтра", "вчера", "сейчас", "потом", "сервер", "леший",
    "леша", "лёша", "ошибка", "ошибку", "проблема", "команда", "команду",
    "проект", "код", "чат", "сайт", "интернет", "погода", "время",
    "добрый", "день", "утро", "вечер", "ночь", "нормально", "хорошо",
))
_EN_WORDS = frozenset(("hello", "hi", "thanks", "thank", "you", "please",
                       "yes", "no", "what", "where", "why", "how", "help",
                       "me", "good", "morning", "night", "test", "server"))


@dataclass(frozen=True, slots=True)
class LayoutSuggestion:
    original: str
    corrected: str
    confidence: float

    @property
    def notice(self) -> str:
        return f"Похоже, вы имели в виду: «{self.corrected}»."


def suggest_keyboard_layout(text: str) -> LayoutSuggestion | None:
    original = str(text or "").strip()
    if not (3 <= len(original) <= 140) or "\n" in original or "`" in original:
        return None
    if _UNSAFE.search(original):
        return None
    latin, cyrillic = bool(_LATIN.search(original)), bool(_CYRILLIC.search(original))
    if latin == cyrillic:
        return None
    translation = _EN_TO_RU if latin else _RU_TO_EN
    lexicon = _RU_WORDS if latin else _EN_WORDS
    converted = original.lower().translate(translation)
    words = _WORD.findall(converted)
    if not words or len(words) > 16:
        return None
    matches = sum(word in lexicon for word in words)
    # One-word messages must be a known greeting/intent; longer messages
    # need enough known words to avoid rewriting random identifiers.
    if matches < (1 if len(words) == 1 else 2):
        return None
    if len(words) > 1 and matches / len(words) < 0.6:
        return None
    corrected = original.translate(translation)
    if corrected == original:
        return None
    return LayoutSuggestion(original, corrected, min(0.99, 0.85 + 0.03 * matches))


def layout_context(text: str) -> str:
    """Trusted system hint for LLM context; original user text stays intact."""
    suggestion = suggest_keyboard_layout(text)
    if suggestion is None:
        return ""
    return (
        "Вероятная ошибка раскладки клавиатуры в последнем сообщении. "
        f"Исходный текст: {suggestion.original!r}. "
        f"Предполагаемый текст: {suggestion.corrected!r}. "
        f"Начни ответ с «{suggestion.notice}», затем ответь по смыслу "
        "исправленного текста. Если контекст указывает на код, имя, адрес "
        "или технический идентификатор, не исправляй его."
    )


__all__ = ["LayoutSuggestion", "suggest_keyboard_layout", "layout_context"]
