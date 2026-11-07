from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import unquote, urlparse

from .research import SearchUnavailable


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    key: str
    title: str
    domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServiceEvidence:
    service: str
    title: str
    url: str
    host: str
    snippet: str
    confidence: float

    def public(self) -> dict[str, object]:
        return {
            "service": self.service,
            "title": self.title,
            "url": self.url,
            "host": self.host,
            "snippet": self.snippet,
            "confidence": self.confidence,
        }


class CallerTagProvider(Protocol):
    def lookup(self, e164: str) -> dict[str, object]: ...


SERVICES: tuple[ServiceDefinition, ...] = (
    ServiceDefinition("telegram", "Telegram", ("t.me", "telegram.me", "telegram.org")),
    ServiceDefinition("whatsapp", "WhatsApp", ("wa.me", "whatsapp.com")),
    ServiceDefinition("vk", "VK", ("vk.com",)),
    ServiceDefinition("ok", "Одноклассники", ("ok.ru",)),
    ServiceDefinition("instagram", "Instagram", ("instagram.com",)),
    ServiceDefinition("facebook", "Facebook", ("facebook.com",)),
    ServiceDefinition("avito", "Avito", ("avito.ru",)),
    ServiceDefinition("linkedin", "LinkedIn", ("linkedin.com",)),
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    normalized = str(host or "").lower().removeprefix("www.")
    return any(normalized == domain or normalized.endswith("." + domain) for domain in domains)


def _contains_phone(title: str, snippet: str, url: str, variants: tuple[str, ...]) -> tuple[bool, float]:
    url_digits = _digits(unquote(url))
    text_digits = _digits(f"{title} {snippet}")
    for variant in variants:
        if not variant:
            continue
        if variant in url_digits:
            return True, 0.84
        if variant in text_digits:
            return True, 0.72
    return False, 0.0


def _number_variants(e164: str, national: str) -> tuple[str, ...]:
    full = _digits(e164)
    local = _digits(national)
    values = [full]
    if len(local) >= 7 and local != full:
        values.append(local)
    # Для международных номеров последние 10 цифр полезны для публичных страниц,
    # где код страны отделён или опущен. Короткие номера так не обрезаем.
    if len(full) > 10:
        values.append(full[-10:])
    return tuple(dict.fromkeys(value for value in values if len(value) >= 7))


class AuthorizedTagFileProvider:
    """Читает теги только из явно предоставленного пользователем локального JSON-файла.

    Это безопасная точка интеграции с официальным экспортом/разрешённым источником.
    Автоматического скрейпинга Getcontact/Truecaller или обхода их защиты здесь нет.
    """

    def __init__(self, path: str | None = None):
        self.path = str(path or os.getenv("LESHY_PHONE_TAGS_FILE", "")).strip()

    def lookup(self, e164: str) -> dict[str, object]:
        if not self.path:
            return {
                "status": "requires_authorized_source",
                "tags": [],
                "source": "none",
                "message": (
                    "Теги из чужих контактных книг требуют официально разрешённого источника. "
                    "Автоматический скрейпинг закрытых каталогов отключён."
                ),
            }
        path = Path(self.path).expanduser()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — пользовательский файл не должен ломать OSINT.
            return {
                "status": "source_error",
                "tags": [],
                "source": "authorized_file",
                "message": f"Не удалось прочитать авторизованный файл тегов: {type(exc).__name__}",
            }

        raw = data.get(e164) if isinstance(data, dict) else None
        if isinstance(raw, str):
            raw = [raw]
        tags: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                value = " ".join(str(item or "").split())[:120]
                if value and value not in tags:
                    tags.append(value)
        return {
            "status": "found" if tags else "no_authorized_tags",
            "tags": tags[:20],
            "source": "authorized_file",
            "message": "Теги получены из явно подключённого пользователем локального источника.",
        }


class PhonePublicFootprint:
    """Ищет только публично индексируемые связи номера с интернет-сервисами."""

    def __init__(self, search_provider, caller_tag_provider: CallerTagProvider | None = None):
        self.search_provider = search_provider
        self.caller_tag_provider = caller_tag_provider or AuthorizedTagFileProvider()

    def analyze(self, *, e164: str, international: str, national: str) -> dict[str, object]:
        variants = _number_variants(e164, national)
        requested_max = str(os.getenv("LESHY_PHONE_OSINT_MAX_SERVICES", "8")).strip()
        try:
            max_services = max(1, min(len(SERVICES), int(requested_max)))
        except ValueError:
            max_services = len(SERVICES)

        service_results: list[dict[str, object]] = []
        for service in SERVICES[:max_services]:
            query = f'site:{service.domains[0]} "{e164}"'
            found: list[ServiceEvidence] = []
            error = ""
            try:
                rows = self.search_provider.search(query, limit=6)
            except SearchUnavailable as exc:
                rows = []
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 — внешний поиск изолирован.
                rows = []
                error = str(exc)

            for row in rows:
                url = str(getattr(row, "url", "") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                host = (urlparse(url).hostname or "").lower().removeprefix("www.")
                if not _host_matches(host, service.domains):
                    continue
                title = " ".join(str(getattr(row, "title", "") or "").split())[:300]
                snippet = " ".join(str(getattr(row, "snippet", "") or "").split())[:700]
                matches, confidence = _contains_phone(title, snippet, url, variants)
                if not matches:
                    continue
                found.append(ServiceEvidence(service.key, title, url, host, snippet, confidence))

            if found:
                service_results.append({
                    "service": service.key,
                    "title": service.title,
                    "status": "public_match",
                    "confidence": max(item.confidence for item in found),
                    "meaning": "номер публично упоминается или связан со страницей сервиса; это не доказывает регистрацию аккаунта",
                    "evidence": [item.public() for item in found[:3]],
                })
            elif error:
                service_results.append({
                    "service": service.key,
                    "title": service.title,
                    "status": "unknown",
                    "confidence": 0.0,
                    "meaning": "поиск недоступен; наличие или отсутствие аккаунта не установлено",
                    "evidence": [],
                    "error": error[:300],
                })
            else:
                service_results.append({
                    "service": service.key,
                    "title": service.title,
                    "status": "no_public_evidence",
                    "confidence": 0.25,
                    "meaning": "публично индексируемого совпадения не найдено; это не означает, что аккаунта нет",
                    "evidence": [],
                })

        caller_tags = self.caller_tag_provider.lookup(e164)
        public_matches = sum(1 for item in service_results if item.get("status") == "public_match")
        return {
            "services": service_results,
            "public_matches": public_matches,
            "caller_tags": caller_tags,
            "method": "public_search",
            "registration_warning": (
                "Публичный поиск может подтвердить только открытое упоминание номера в сервисе. "
                "Он не доказывает сам факт регистрации и не использует восстановление пароля, signup-enumeration или закрытые базы."
            ),
        }


__all__ = [
    "AuthorizedTagFileProvider",
    "CallerTagProvider",
    "PhonePublicFootprint",
    "SERVICES",
]
