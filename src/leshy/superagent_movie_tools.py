"""Owner-scoped movie discovery -> Parser for Plex handoff.

The LLM can suggest titles, but only this deterministic adapter may submit
verified items. External snippets cannot grant tool permission; the ticket's
original owner mandate and the existing Parser pairing both must match.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit
from typing import Any


_FILM = re.compile(r"(?iu)\b(?:фильм\w*|кинопремьер\w*|премьер\w*|киноновин\w*|movie\w*|films?)\b")
_DISCOVERY = re.compile(
    r"(?iu)\b(?:найди|найти|ищи|поищи|подбери|отыщи|скачай|скачать|"
    r"загрузи|загрузить|добавь|отправь|закинь|поставь|найденн\w*|нов\w*)\b"
)
_NO_QUEUE = re.compile(r"(?iu)(?:не\s+(?:скачивай|загружай|добавляй|отправляй)|только\s+(?:список|расскажи|обзор))")


def movie_handoff_mandate(request: str) -> bool:
    """Only an explicit SuperAgent movie-finding assignment enables submission."""
    value = " ".join(str(request or "").split())
    return bool(_FILM.search(value) and _DISCOVERY.search(value) and not _NO_QUEUE.search(value))


def _normalize_title(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(text or "").casefold()))


def _public_https_url(raw: str) -> bool:
    try:
        url = urlsplit(raw)
        host = str(url.hostname or "").casefold()
        return (url.scheme == "https" and bool(host) and "." in host
                and not url.username and not url.password
                and url.port in (None, 443) and len(raw) <= 1000
                and not host.endswith((".local", ".internal", ".invalid", ".test"))
                and host not in {"localhost"})
    except (ValueError, TypeError):
        return False


def validated_movie_items(arguments: dict[str, Any],
                          evidence: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Match titles and years to *actual* current search results."""
    items = arguments.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 5:
        raise ValueError("Передайте от одного до пяти фильмов с подтверждающими источниками")
    indexed = {
        str(row.get("url") or ""): row
        for row in evidence if isinstance(row, dict) and _public_https_url(str(row.get("url") or ""))
    }
    used: set[tuple[str, int]] = set()
    ready = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Некорректная запись фильма")
        title = " ".join(str(item.get("title") or "").split())
        url = str(item.get("source_url") or "")
        year = item.get("year")
        if not 2 <= len(title) <= 180 or type(year) is not int or not 1900 <= year <= 2100:
            raise ValueError("Для каждого фильма нужны название и подтверждённый год")
        if re.search(r"[\r\n\x00-\x1f]", title):
            raise ValueError("Некорректное название фильма")
        hit = indexed.get(url)
        if hit is None:
            raise ValueError("Источник фильма не найден среди результатов текущего поиска")
        witness = " ".join([str(hit.get("title") or ""), str(hit.get("snippet") or "")])
        normalized_title = _normalize_title(title)
        normalized_witness = _normalize_title(witness)
        if (not normalized_title or
                not re.search(r"(?<!\w)" + re.escape(normalized_title) + r"(?!\w)", normalized_witness)
                or not re.search(r"(?<!\d)" + str(year) + r"(?!\d)", witness)):
            raise ValueError("Название или год не подтверждены выбранным источником")
        key = (normalized_title, year)
        if key in used:
            continue
        used.add(key)
        ready.append({"title": title, "year": year, "source_url": url})
    if not ready:
        raise ValueError("Нет уникальных подтверждённых фильмов")
    return ready


def handoff_movies(app: Any, owner_id: str, request: str,
                   arguments: dict[str, Any], evidence: list[dict[str, str]],
                   *, still_active=lambda: True) -> list[dict[str, str]]:
    """Use Leshy's established pairing. Never return NAS secrets or raw errors."""
    if not movie_handoff_mandate(request):
        raise PermissionError("Исходное поручение не разрешает передачу фильмов в парсер")
    verified = validated_movie_items(arguments, evidence)
    from .torrent_parser_bridge import TorrentParserBridge
    from .torrent_parser_chat import authorized_owner_for_parser

    settings = getattr(app, "settings", None)
    if settings is None or not getattr(settings, "brain_dir", None):
        raise RuntimeError("Нет настроек Parser for Plex в профиле Лешего")
    bridge = TorrentParserBridge(settings.brain_dir)
    linked = authorized_owner_for_parser(app, bridge, owner_id)
    if not bridge.owner_allowed(linked):
        raise PermissionError("Профиль Суперагента не привязан к Parser for Plex")
    output = []
    for item in verified:
        if not still_active():
            output.append({"title": item["title"], "status": "cancelled"})
            break
        # Existing NAS service deduplicates on normalized kind/title.
        name = f'{item["title"]} {item["year"]}'
        try:
            result = bridge.submit(linked, "movie", name)
        except (RuntimeError, ValueError, ConnectionError, PermissionError, OSError):
            output.append({"title": name, "status": "failed", "job_id": ""})
            break  # Unknown network outcome: do not blindly submit other films.
        status = str(result.get("status") or "")
        job_id = str(result.get("job_id") or "")
        if status not in {"queued", "searching", "ready"} or not job_id:
            output.append({"title": name, "status": "rejected", "job_id": ""})
            break
        output.append({"title": name, "status": status, "job_id": job_id[:220]})
    return output
