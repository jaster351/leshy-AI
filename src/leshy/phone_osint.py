from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
import re
from urllib.parse import urlparse

import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType, carrier, geocoder, timezone

from .diagnostics import event, problem
from .research import SearchUnavailable


_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().\-]{6,}\d)(?!\d)")
_INTENT_MARKERS = (
    "/phone", "/номер", "кто звонил", "кто звонит", "что за номер",
    "проверь номер", "проверить номер", "пробей номер", "пробить номер",
    "определи номер", "определить номер", "caller id", "phone osint",
)
_SPAM_MARKERS = (
    "спам", "мошенник", "мошеннич", "обман", "нежелательный звонок", "реклама",
    "робозвон", "автообзвон", "коллектор", "spam", "scam", "fraud", "robocall",
    "telemark", "nuisance", "unwanted call", "phishing", "vishing",
)
_STRONG_FRAUD_MARKERS = (
    "мошенник", "мошеннич", "обман", "вымог", "подмена номера", "scam", "fraud",
    "phishing", "vishing", "impersonation", "spoofed",
)
_BUSINESS_MARKERS = (
    "ооо", "ип ", "компания", "организация", "магазин", "банк", "клиника", "сервис",
    "служба", "доставка", "такси", "отель", "гостиница", "ресторан", "официальный сайт",
    "контакты", "business", "company", "official", "contact", "support", "customer service",
    "llc", "ltd", "inc.",
)
_PRIVATE_DATA_MARKERS = (
    "утечк", "слитая база", "паспорт", "адрес проживания", "родственник", "dox",
    "breach database", "leak database", "leaked database",
)


@dataclass(frozen=True, slots=True)
class PhoneMetadata:
    raw: str
    e164: str
    international: str
    national: str
    country_code: int
    region_code: str
    region_name: str
    carrier_name: str
    line_type: str
    timezones: tuple[str, ...]
    possible: bool
    valid: bool


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str
    title: str
    url: str
    host: str
    snippet: str
    strong_fraud: bool = False

    def public(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "host": self.host,
            "snippet": self.snippet,
            "strong_fraud": self.strong_fraud,
        }


_TYPE_NAMES = {
    PhoneNumberType.FIXED_LINE: "стационарный",
    PhoneNumberType.MOBILE: "мобильный",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "стационарный или мобильный",
    PhoneNumberType.TOLL_FREE: "бесплатная линия",
    PhoneNumberType.PREMIUM_RATE: "платная линия",
    PhoneNumberType.SHARED_COST: "линия с разделением стоимости",
    PhoneNumberType.VOIP: "VoIP",
    PhoneNumberType.PERSONAL_NUMBER: "персональный номер",
    PhoneNumberType.PAGER: "пейджер",
    PhoneNumberType.UAN: "универсальный номер доступа",
    PhoneNumberType.VOICEMAIL: "голосовая почта",
    PhoneNumberType.UNKNOWN: "неизвестный тип",
}


def is_phone_osint_request(text: str) -> bool:
    value = " ".join(str(text or "").lower().split())
    return any(marker in value for marker in _INTENT_MARKERS) and _PHONE_RE.search(value) is not None


def extract_phone_candidate(text: str) -> str:
    match = _PHONE_RE.search(str(text or ""))
    if match is None:
        raise ValueError("Не найден телефонный номер. Укажите его с кодом страны, например: /phone +7 900 123-45-67")
    value = match.group(1).strip()
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7 or len(digits) > 15:
        raise ValueError("Номер выглядит некорректно: допустимо от 7 до 15 цифр")
    return value


def _parse_number(raw: str) -> tuple[PhoneMetadata, object]:
    default_region = os.getenv("LESHY_PHONE_DEFAULT_REGION", "RU").strip().upper() or "RU"
    parsed = phonenumbers.parse(raw, None if raw.strip().startswith("+") else default_region)
    possible = phonenumbers.is_possible_number(parsed)
    valid = phonenumbers.is_valid_number(parsed)
    region_code = phonenumbers.region_code_for_number(parsed) or ""
    return (
        PhoneMetadata(
            raw=raw,
            e164=phonenumbers.format_number(parsed, PhoneNumberFormat.E164),
            international=phonenumbers.format_number(parsed, PhoneNumberFormat.INTERNATIONAL),
            national=phonenumbers.format_number(parsed, PhoneNumberFormat.NATIONAL),
            country_code=int(parsed.country_code),
            region_code=region_code,
            region_name=geocoder.description_for_number(parsed, "ru") or geocoder.description_for_number(parsed, "en") or "",
            carrier_name=carrier.name_for_number(parsed, "ru") or carrier.name_for_number(parsed, "en") or "",
            line_type=_TYPE_NAMES.get(phonenumbers.number_type(parsed), "неизвестный тип"),
            timezones=tuple(timezone.time_zones_for_number(parsed)),
            possible=bool(possible),
            valid=bool(valid),
        ),
        parsed,
    )


def _number_log_id(e164: str) -> str:
    return sha256(str(e164).encode("utf-8")).hexdigest()[:16]


def _classify_result(title: str, snippet: str, host: str) -> tuple[str, bool]:
    haystack = f"{title} {snippet} {host}".lower()
    if any(marker in haystack for marker in _PRIVATE_DATA_MARKERS):
        return "private-data", False
    strong = any(marker in haystack for marker in _STRONG_FRAUD_MARKERS)
    if strong or any(marker in haystack for marker in _SPAM_MARKERS):
        return "fraud-report", strong
    if any(marker in haystack for marker in _BUSINESS_MARKERS):
        return "business", False
    return "other", False


def _search_queries(meta: PhoneMetadata) -> list[str]:
    digits = re.sub(r"\D", "", meta.e164)
    national_digits = re.sub(r"\D", "", meta.national)
    values = [
        f'"{meta.e164}"',
        f'"{meta.international}"',
        f'"{digits}" мошенники спам отзывы',
        f'"{digits}" scam spam fraud',
    ]
    if national_digits and national_digits != digits:
        values.append(f'"{national_digits}" мошенники спам отзывы')
    return list(dict.fromkeys(values))[:5]


def _risk_score(meta: PhoneMetadata, evidence: list[Evidence]) -> tuple[int, float, str, list[str]]:
    fraud = [item for item in evidence if item.kind == "fraud-report"]
    business = [item for item in evidence if item.kind == "business"]
    fraud_hosts = {item.host for item in fraud if item.host}
    business_hosts = {item.host for item in business if item.host}
    strong_count = sum(1 for item in fraud if item.strong_fraud)

    score = 0
    reasons: list[str] = []
    if not meta.possible:
        score += 35
        reasons.append("номер не проходит проверку возможного диапазона")
    elif not meta.valid:
        score += 20
        reasons.append("номер имеет корректную структуру, но не считается валидным для своего диапазона")

    if meta.line_type == "платная линия":
        score += 12
        reasons.append("номер относится к платной линии")

    if fraud:
        score += min(55, len(fraud) * 14)
        reasons.append(f"найдено публичных негативных упоминаний: {len(fraud)}")
    if len(fraud_hosts) >= 2:
        score += 12
        reasons.append("негативные сигналы подтверждаются независимыми доменами")
    if len(fraud_hosts) >= 3:
        score += 8
    if strong_count:
        score += min(18, strong_count * 6)
        reasons.append(f"явных упоминаний мошенничества: {strong_count}")

    # Публичная принадлежность организации уменьшает риск только немного:
    # Caller ID может быть подменён, поэтому бизнес-совпадение не является доказательством звонящего.
    if business_hosts and not fraud:
        score -= min(15, 5 + len(business_hosts) * 3)
        reasons.append(f"номер встречается в публичных контактах организаций: {len(business_hosts)} источников")

    score = max(0, min(100, score))
    useful_hosts = fraud_hosts | business_hosts
    confidence = min(0.95, 0.20 + len(useful_hosts) * 0.14 + min(0.20, len(evidence) * 0.025)) if evidence else 0.12

    if score >= 70:
        verdict = "высокий риск мошенничества"
    elif score >= 45:
        verdict = "повышенный риск"
    elif score >= 20:
        verdict = "есть настораживающие признаки"
    elif business_hosts and not fraud:
        verdict = "публично связан с организацией, явных антифрод-сигналов не найдено"
    elif evidence:
        verdict = "явных антифрод-сигналов недостаточно"
    else:
        verdict = "данных недостаточно"
    return score, round(confidence, 2), verdict, reasons


class PhoneOsintService:
    """Пассивный антифрод OSINT по телефонному номеру только из открытых источников."""

    def __init__(self, app):
        self.app = app

    def analyze(self, text: str) -> dict[str, object]:
        raw = extract_phone_candidate(text)
        try:
            meta, _parsed = _parse_number(raw)
        except phonenumbers.NumberParseException as exc:
            raise ValueError(f"Не удалось разобрать номер: {exc}") from exc

        number_hash = _number_log_id(meta.e164)
        event("phone_osint_started", number_hash=number_hash)
        by_url: dict[str, Evidence] = {}
        errors: list[str] = []
        provider = self.app.research.search_provider

        for query_index, query in enumerate(_search_queries(meta), start=1):
            try:
                rows = provider.search(query, limit=7)
            except SearchUnavailable as exc:
                errors.append(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 — внешние поисковые провайдеры изолируем.
                problem(
                    "phone_osint_search_failed",
                    str(exc),
                    number_hash=number_hash,
                    query_variant=query_index,
                )
                errors.append(str(exc))
                continue

            for row in rows:
                url = str(getattr(row, "url", "") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                title = " ".join(str(getattr(row, "title", "") or "").split())[:300]
                snippet = " ".join(str(getattr(row, "snippet", "") or "").split())[:900]
                host = (urlparse(url).hostname or "").lower().removeprefix("www.")
                kind, strong = _classify_result(title, snippet, host)
                # Не используем утечки/доксинг и не показываем нейтральные совпадения,
                # которые могут случайно деанонимизировать частного человека.
                if kind in {"private-data", "other"}:
                    continue
                by_url.setdefault(url, Evidence(kind, title, url, host, snippet, strong))

        evidence = list(by_url.values())[:30]
        score, confidence, verdict, reasons = _risk_score(meta, evidence)
        result = {
            "number": {
                "raw": meta.raw,
                "e164": meta.e164,
                "international": meta.international,
                "national": meta.national,
                "country_code": meta.country_code,
                "region_code": meta.region_code,
                "region_name": meta.region_name,
                "carrier": meta.carrier_name,
                "line_type": meta.line_type,
                "timezones": list(meta.timezones),
                "possible": meta.possible,
                "valid": meta.valid,
            },
            "risk_score": score,
            "confidence": confidence,
            "verdict": verdict,
            "reasons": reasons,
            "evidence": [item.public() for item in evidence],
            "search_errors": errors[:3],
            "caller_id_spoofing_warning": (
                "Номер в Caller ID может быть подменён. OSINT оценивает репутацию отображённого номера, "
                "но не доказывает личность фактического звонящего."
            ),
            "privacy_mode": "Только открытые источники; утечки, закрытые базы, адреса, паспорта и родственники не используются.",
        }
        event(
            "phone_osint_finished",
            number_hash=number_hash,
            risk_score=score,
            confidence=confidence,
            evidence=len(evidence),
        )
        return result

    @staticmethod
    def format_answer(result: dict[str, object]) -> str:
        number = dict(result.get("number") or {})
        score = int(result.get("risk_score") or 0)
        confidence = float(result.get("confidence") or 0.0)
        evidence = list(result.get("evidence") or [])
        reasons = list(result.get("reasons") or [])
        fraud = [item for item in evidence if item.get("kind") == "fraud-report"]
        business = [item for item in evidence if item.get("kind") == "business"]

        lines = [
            f"Проверил {number.get('international') or number.get('e164')}. Антифрод-риск: {score}/100 — {result.get('verdict')}.",
            f"Уверенность оценки: {int(confidence * 100)}%.",
        ]
        meta = []
        if number.get("region_name"):
            meta.append(str(number["region_name"]))
        if number.get("carrier"):
            meta.append(str(number["carrier"]))
        if number.get("line_type"):
            meta.append(str(number["line_type"]))
        if meta:
            lines.append("Метаданные номера: " + ", ".join(meta) + ".")
        if reasons:
            lines.append("Почему такая оценка: " + "; ".join(str(x) for x in reasons) + ".")
        if fraud:
            lines.append("Негативные публичные сигналы:")
            for item in fraud[:6]:
                lines.append(f"• {item.get('title') or item.get('host')} — {item.get('url')}")
        if business:
            lines.append("Публичные контакты организаций:")
            for item in business[:4]:
                lines.append(f"• {item.get('title') or item.get('host')} — {item.get('url')}")
        if not evidence:
            lines.append("Надёжных публичных совпадений не найдено. Это не означает, что звонок безопасен.")
        lines.append(str(result.get("caller_id_spoofing_warning") or ""))
        lines.append("Не сообщайте коды из SMS, пароли и данные карты только на основании определившегося номера.")
        return "\n".join(line for line in lines if line)


__all__ = ["PhoneOsintService", "extract_phone_candidate", "is_phone_osint_request"]
