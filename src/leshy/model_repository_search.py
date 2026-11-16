"""Live, read-only Hugging Face model repository discovery.

Model titles, publication dates, tags and links come from Hub responses.
Profession labels are *metadata hints*, never a claim of professional ability.
This module NEVER downloads, runs or activates search results.
"""
from __future__ import annotations

from datetime import date
import json
import re
from typing import Any
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


HUB_MODELS_ENDPOINT = "https://huggingface.co/api/models"
MAX_RESPONSE_BYTES = 4_000_000
SCAN_LIMIT = 120
PROFESSIONS = {
    "all": {"label": "Все специализации", "query": "", "terms": ()},
    "programming": {
        "label": "Программирование", "query": "coder",
        "terms": ("coder", "coding", "code", "programming", "software", "developer"),
    },
    "legal": {
        "label": "Юриспруденция", "query": "legal",
        "terms": ("legal", "law", "lawyer", "juris", "attorney"),
    },
    "medicine": {
        "label": "Медицина", "query": "medical",
        "terms": ("medical", "medicine", "healthcare", "doctor", "clinical", "medic"),
    },
    "finance": {
        "label": "Финансы", "query": "finance",
        "terms": ("finance", "financial", "fintech", "accounting", "banking"),
    },
    "science": {
        "label": "Наука", "query": "science",
        "terms": ("science", "scientific", "research", "physics", "chemistry"),
    },
    "education": {
        "label": "Образование", "query": "education",
        "terms": ("education", "educational", "teacher", "tutor", "teaching"),
    },
}
_VALID_ID = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
_VALID_AUTHOR = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")
_WORD = re.compile(r"[\w]+", re.UNICODE)


def _parse_date(value: str, field: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"Неверная дата {field}: нужен формат ГГГГ-ММ-ДД")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Некорректная дата {field}") from exc


def _matches_profession(entry: dict[str, Any], specialty: str) -> bool:
    if specialty == "all":
        return True
    tags = entry.get("tags")
    if not isinstance(tags, list):
        tags = []
    # Only explicit model id and published tags are signals. Do not infer
    # competence from model family, author or a generated description.
    tokens = set(_WORD.findall(
        (str(entry.get("id") or "") + " " + " ".join(str(x) for x in tags)).casefold()
    ))
    return any(term in tokens for term in PROFESSIONS[specialty]["terms"])


def search_hub_models(
    *,
    name: str = "",
    author: str = "",
    specialty: str = "all",
    date_from: str = "",
    date_to: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    """Search *current* Hub repositories; date range means first publication.

    The Hub endpoint provides a bounded most-recent slice, not an exhaustive
    historical index. Unknown publication dates are excluded when dates are set.
    """
    name = str(name or "").strip()
    author = str(author or "").strip()
    specialty = str(specialty or "all").strip().lower()
    if len(name) > 120 or any(ord(ch) < 32 for ch in name):
        raise ValueError("Название модели слишком длинное или содержит управляющие символы")
    if author and not _VALID_AUTHOR.fullmatch(author):
        raise ValueError("Некорректное имя автора или организации")
    if specialty not in PROFESSIONS:
        raise ValueError("Неизвестная специализация")
    first = _parse_date(date_from, "от")
    last = _parse_date(date_to, "до")
    if first and last and first > last:
        raise ValueError("Дата «от» не должна быть позже даты «до»")
    try:
        limit = int(limit)
    except (ValueError, TypeError) as exc:
        raise ValueError("Неверное количество результатов") from exc
    if limit < 1 or limit > 60:
        raise ValueError("Количество результатов должно быть от 1 до 60")

    # Search the actual repository identifiers; profession is a metadata
    # qualifier only if the user did not specify a model name.
    query = name or (PROFESSIONS[specialty]["query"] if specialty != "all" else "")
    params: dict[str, str | int] = {
        "sort": "createdAt", "direction": -1, "limit": SCAN_LIMIT,
        "full": "true",
    }
    if query:
        params["search"] = query
    if author:
        params["author"] = author
    url = HUB_MODELS_ENDPOINT + "?" + urlencode(params)
    req = Request(url, headers={
        "Accept": "application/json", "User-Agent": "Leshy-AI/model-repository-search",
    })
    with urlopen(req, timeout=18) as response:
        if not str(response.geturl()).startswith(HUB_MODELS_ENDPOINT):
            raise ValueError("Модельный каталог перенаправил запрос на другой адрес")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Ответ каталога моделей превышает допустимый размер")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Каталог моделей вернул неожиданный формат")
    items: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not _VALID_ID.fullmatch(model_id):
            continue
        if name and name.casefold() not in model_id.casefold():
            continue
        if author and model_id.split("/", 1)[0].casefold() != author.casefold():
            continue
        if not _matches_profession(entry, specialty):
            continue
        created = str(entry.get("createdAt") or "")[:10]
        try:
            published = date.fromisoformat(created)
        except ValueError:
            published = None
        if (first or last) and published is None:
            continue
        if published and ((first and published < first) or (last and published > last)):
            continue
        tags = entry.get("tags")
        tags = tags if isinstance(tags, list) else []
        model_url = "https://huggingface.co/" + quote(model_id, safe="/")
        items.append({
            "id": model_id,
            "name": model_id.split("/", 1)[1],
            "author": model_id.split("/", 1)[0],
            "url": model_url,
            "created_at": created if published else "",
            "last_modified": str(entry.get("lastModified") or "")[:10],
            "downloads": max(0, int(entry.get("downloads") or 0)),
            "pipeline_tag": str(entry.get("pipeline_tag") or "")[:80],
            "gguf_tag": "gguf" in {str(x).casefold() for x in tags},
            "gated": bool(entry.get("gated")),
            "license": next(
                (str(tag)[8:] for tag in tags if isinstance(tag, str) and tag.startswith("license:")),
                "",
            ),
            "specialty_match": "Название/теги репозитория" if specialty != "all" else "",
        })
        if len(items) >= limit:
            break
    return {
        "source": "Hugging Face Hub API",
        "query": name,
        "author": author,
        "specialty": specialty,
        "date_from": date_from,
        "date_to": date_to,
        "items": items,
        "count": len(items),
        "scanned": len(payload),
        "scan_limited": len(payload) >= SCAN_LIMIT,
        "exhaustive": False,
        "note": ("Поиск ограничен последними репозиториями, подходящими под запрос. "
                 "Специализация определяется по названию и тегам, а не по проверенным навыкам. "
                 "Поисковая выдача сама по себе не подтверждает совместимость с GGUF/runtime."),
    }


__all__ = ["search_hub_models", "PROFESSIONS"]
