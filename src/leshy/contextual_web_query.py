"""Связь поискового запроса с контекстом диалога и предоставленными URL.

Модуль не открывает страницы и не объявляет поисковую выдачу проверенным источником.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from .source_reading import supplied_public_url

_FOLLOWUP = re.compile(
    r"(?i)(?:зайди|посети|посмотри|проверь|открой|первоначальн(?:ый|ому|ое)|"
    r"вернись к вопросу|ответь на (?:мой|первый|исходный) вопрос|"
    r"официальн(?:ый|ом|ого|ую) сайт)"
)
_WORD = re.compile(r"[^\w\s-]+", re.UNICODE)
_IGNORE = {"article", "newswire", "index", "html", "www"}
_IGNORED_CONTEXT = re.compile(
    r"(?i)^(?:спасибо|окей|хорошо|продолжай|да|нет|"
    r"зайди|посмотри|проверь|открой|я тебя понял|вернись|ответь)"
)


def url_search_query(message: str) -> str | None:
    """Преобразовать предоставленный URL в тему поиска без технического шума."""
    url = supplied_public_url(message)
    if not url:
        return None
    parts = urlsplit(url)
    host = str(parts.hostname or "").lower()
    if not host:
        return None
    # Параметры вроде ?id=1%27 и HTTPS-префикс не являются поисковой темой.
    path = unquote(parts.path)
    words = [word.lower() for word in _WORD.sub(" ", path.replace("-", " ")).split()]
    words = [word for word in words if word not in _IGNORE and len(word) >= 2]
    suffix = " ".join(words[-18:])[:180]
    return f"site:{host} {suffix}".strip() if suffix else f"site:{host}"


def contextual_search_query(message: str, history: list) -> tuple[str, bool]:
    """Получить осмысленный запрос, используя только историю ЭТОЙ сессии.

    История передаётся вызывающим кодом из сессии с проверенным owner_id.
    Возвращаемый флаг требует онлайн-проверки для ссылок и явных продолжений.
    """
    text = str(message or "").strip()
    url_query = url_search_query(text)
    if url_query:
        return url_query, True
    if not _FOLLOWUP.search(text):
        return text, False
    for row in history[1:16]:
        try:
            if row["role"] != "user":
                continue
            prior = str(row["content"] or "").strip()
        except (KeyError, TypeError, IndexError):
            continue
        if not prior or prior == text:
            continue
        prior_url = url_search_query(prior)
        if prior_url:
            return prior_url, True
        if len(prior) >= 18 and not _IGNORED_CONTEXT.match(prior):
            return f"{prior[:210]} официальный источник", True
    return text, False


def relevant_url_search_results(query: str, results: list[dict]) -> list[dict]:
    """Удалить случайные результаты о HTTPS вместо темы предоставленного URL."""
    if not str(query).lower().startswith("site:"):
        return results
    terms = re.findall(r"[\w]+", query.split(" ", 1)[-1].lower())
    topic = [term for term in terms if len(term) >= 4 and term not in _IGNORE]
    if not topic:
        return results
    selected = []
    for item in results:
        combined = " ".join(str(item.get(key) or "") for key in ("title", "snippet", "url")).lower()
        matched = sum(term in combined for term in set(topic))
        if matched >= min(2, len(set(topic))):
            selected.append(item)
    return selected


__all__ = ["contextual_search_query", "url_search_query", "relevant_url_search_results"]
