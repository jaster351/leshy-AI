from __future__ import annotations

"""Фактическая погода из интернет-API; локальная LLM не придумывает прогноз."""

import json
import os
import re
import ssl
from urllib import error, parse, request

from .diagnostics import problem

_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_WTTR = "https://wttr.in/"
_CITY_ALIASES = {
    "челябинске": "Челябинск",
    "москве": "Москва",
    "питере": "Санкт-Петербург",
    "санкт-петербурге": "Санкт-Петербург",
    "екатеринбурге": "Екатеринбург",
    "казани": "Казань",
    "тюмени": "Тюмень",
    "новосибирске": "Новосибирск",
    "перми": "Пермь",
    "уфе": "Уфа",
    "сочи": "Сочи",
    "амстердаме": "Амстердам",
}
_WMO = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность",
    3: "пасмурно", 45: "туман", 48: "изморозь и туман",
    51: "слабая морось", 53: "морось", 55: "сильная морось",
    56: "переохлаждённая морось", 57: "сильная переохлаждённая морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "переохлаждённый дождь", 67: "сильный переохлаждённый дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна",
    80: "небольшой ливень", 81: "ливень", 82: "сильный ливень",
    85: "небольшой снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}
_WTTR_CODES = {
    113: "ясно", 116: "переменная облачность", 119: "облачно",
    122: "пасмурно", 143: "туман", 176: "местами дождь",
    179: "местами снег", 200: "гроза", 227: "метель",
    248: "туман", 260: "переохлаждённый туман",
    263: "небольшой дождь", 266: "морось", 296: "небольшой дождь",
    299: "дождь", 302: "дождь", 305: "сильный дождь", 308: "ливень",
    326: "снег", 329: "снег", 332: "снег", 335: "сильный снег",
    338: "сильный снег", 353: "ливень", 356: "ливень",
    368: "снегопад", 371: "сильный снегопад", 386: "гроза",
    389: "гроза с дождём", 392: "гроза со снегом", 395: "сильный снегопад",
}
_NON_CITY = {
    "интернете", "сети", "окне", "городе", "этом", "помещении",
    "приложении", "улице", "доме", "моём", "моем", "стране",
    "сегодня", "завтра", "сейчас", "будет", "ближайшие",
}


def is_weather_request(text: str) -> bool:
    """Отличаем погоду от температуры CPU и похожих технических запросов."""
    q = " ".join(str(text or "").lower().split())
    return bool(re.search(
        r"\b(?:погод\w*|weather|прогноз\s+погод\w*|"
        r"сколько\s+градусов\s+на\s+улице|температура\s+на\s+улице)\b", q,
    ))


def _city(text: str) -> str:
    q = " ".join(str(text or "").strip().split())
    # Сначала явное «в/для города», не ошибочно «в интернете»/«на улице».
    candidates = re.findall(
        r"\b(?:в|во|для|in)\s+([А-Яа-яЁёA-Za-z-]{2,48})",
        q, flags=re.IGNORECASE,
    )
    for candidate in reversed(candidates):
        if candidate.lower() not in _NON_CITY:
            return _CITY_ALIASES.get(candidate.lower(), candidate)
    # Дополнительно: «погода Челябинск», «weather London».
    direct = re.search(
        r"\b(?:погод\w*|weather)\s+([А-Яа-яЁёA-Za-z-]{2,48})\b",
        q, flags=re.IGNORECASE,
    )
    if direct:
        candidate = direct.group(1)
        if candidate.lower() not in _NON_CITY and candidate.lower() not in {"на", "в", "во", "для", "по"}:
            return _CITY_ALIASES.get(candidate.lower(), candidate)
    return os.getenv("LESHY_WEATHER_CITY", "").strip()[:80]


def _get_json(endpoint: str, params: dict[str, str], *, timeout: int = 7) -> dict:
    url = endpoint + "?" + parse.urlencode(params)
    req = request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Leshy-AI/Weather/2",
    })
    # Прокси окружения работает штатно; дополнительный CA — только доверенный
    # администратором PEM. Проверку сертификатов TLS никогда не отключаем.
    context = ssl.create_default_context()
    ca_bundle = os.getenv("LESHY_WEATHER_CA_BUNDLE", "").strip()
    if ca_bundle:
        context.load_verify_locations(cafile=ca_bundle)
    with request.urlopen(req, timeout=timeout, context=context) as response:
        if response.status != 200:
            raise RuntimeError("Погодный сервис вернул HTTP " + str(response.status))
        raw = response.read(512_001)
    if len(raw) > 512_000:
        raise RuntimeError("Ответ погодного сервиса слишком большой")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or value.get("error"):
        raise RuntimeError("Погодный сервис сообщил ошибку")
    return value


def _number(value: object, *, unit: str = "", precision: int = 0) -> str:
    if value is None or isinstance(value, bool):
        return "нет данных"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "нет данных"
    if not (-100000 < number < 100000):
        return "нет данных"
    return f"{number:.{precision}f}".replace(".", ",") + unit


def _open_meteo_answer(text: str, city: str) -> str:
    geocoded = _get_json(_GEOCODING, {
        "name": city, "count": "1", "language": "ru", "format": "json",
    })
    matches = geocoded.get("results") or []
    if not isinstance(matches, list) or not matches:
        return f"Не удалось определить город «{city}». Уточните город и страну."
    location = matches[0]
    lat = float(location["latitude"])
    lon = float(location["longitude"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Сервис вернул некорректные координаты")
    data = _get_json(_FORECAST, {
        "latitude": str(lat), "longitude": str(lon), "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,precipitation,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "forecast_days": "2",
    })
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    name = str(location.get("name") or city)
    country = str(location.get("country") or "")
    title = name + (", " + country if country else "")
    tomorrow = bool(re.search(r"\bзавтра\b", str(text).lower()))
    index = 1 if tomorrow else 0
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    chance = daily.get("precipitation_probability_max") or []
    conditions = daily.get("weather_code") or []
    if len(dates) <= index or len(highs) <= index or len(lows) <= index:
        raise ValueError("Погодный сервис вернул неполный прогноз")
    label = "Завтра" if tomorrow else "Сегодня"
    code = int(conditions[index]) if len(conditions) > index and conditions[index] is not None else -1
    lines = [
        f"{label} в {title} ({dates[index]}): {_WMO.get(code, 'погодные условия не определены')}.",
        f"Температура: от {_number(lows[index], unit=' °C')} до {_number(highs[index], unit=' °C')}.",
    ]
    if len(chance) > index:
        lines.append("Максимальная вероятность осадков: " + _number(chance[index], unit=" %") + ".")
    if not tomorrow:
        now_code = current.get("weather_code")
        condition = _WMO.get(int(now_code), "условия не определены") if now_code is not None else "условия не определены"
        lines.append(
            f"Сейчас ({current.get('time') or 'время не указано'}): "
            f"{_number(current.get('temperature_2m'), unit=' °C')}, {condition}; "
            f"ощущается как {_number(current.get('apparent_temperature'), unit=' °C')}; "
            f"ветер {_number(current.get('wind_speed_10m'), unit=' км/ч')}; "
            f"осадки {_number(current.get('precipitation'), unit=' мм')}."
        )
    lines.append("Источник: Open-Meteo (прогноз по метеомоделям): https://open-meteo.com/en/docs")
    return "\n".join(lines)


def _wttr_answer(text: str, city: str) -> str:
    # Без города wttr.in определяет ПРИБЛИЗИТЕЛЬНЫЙ город по публичному IP;
    # при VPN/прокси он может не совпасть с реальным местонахождением.
    endpoint = _WTTR + parse.quote(city, safe="") if city else _WTTR.rstrip("/")
    data = _get_json(endpoint, {"format": "j1", "lang": "ru"}, timeout=8)
    areas = data.get("nearest_area") or []
    area = areas[0] if isinstance(areas, list) and areas and isinstance(areas[0], dict) else {}
    def area_value(key: str) -> str:
        items = area.get(key) or []
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return str(items[0].get("value") or "").strip()
        return ""
    resolved = area_value("areaName")
    country = area_value("country")
    location = resolved or city
    if not location:
        raise ValueError("Сервис не определил город по IP")
    place = location + (", " + country if country else "")
    tomorrow = bool(re.search(r"\bзавтра\b", str(text).lower()))
    days = data.get("weather") or []
    index = 1 if tomorrow else 0
    if not isinstance(days, list) or len(days) <= index or not isinstance(days[index], dict):
        raise ValueError("Нет прогноза на запрошенный день")
    day = days[index]
    if day.get("mintempC") is None or day.get("maxtempC") is None:
        raise ValueError("Не хватает данных о температуре")
    label = "Завтра" if tomorrow else "Сегодня"
    lines = [
        f"{label} в {place} ({day.get('date') or 'дата не указана'}): "
        f"от {_number(day['mintempC'], unit=' °C')} до {_number(day['maxtempC'], unit=' °C')}."
    ]
    if not tomorrow:
        observations = data.get("current_condition") or []
        current = observations[0] if isinstance(observations, list) and observations and isinstance(observations[0], dict) else {}
        if current.get("temp_C") is not None:
            try:
                code = int(current.get("weatherCode") or -1)
            except (TypeError, ValueError):
                code = -1
            desc = _WTTR_CODES.get(code, "условия не определены")
            timestamp = current.get("localObsDateTime") or current.get("observation_time") or "время не указано"
            lines.append(
                f"По последнему наблюдению ({timestamp}): "
                f"{_number(current.get('temp_C'), unit=' °C')}, {desc}; "
                f"ощущается как {_number(current.get('FeelsLikeC'), unit=' °C')}; "
                f"ветер {_number(current.get('windspeedKmph'), unit=' км/ч')}."
            )
    if not city:
        lines.append("Город определён приблизительно по публичному IP. При VPN или прокси укажите нужный город.")
    lines.append("Источник: wttr.in (резервный интернет-сервис): https://wttr.in/")
    return "\n".join(lines)


def _tls_error(exc: Exception) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return True
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


def weather_answer(text: str) -> str | None:
    """Прогноз с источником; при недоступности сервиса — честное объяснение."""
    if not is_weather_request(text):
        return None
    city = _city(text)
    failures: list[tuple[str, Exception]] = []
    # При отсутствии заданного города не выдаём догадки LLM и не спрашиваем
    # пользователя сразу: пробуем приблизительное IP-определение через API.
    if city:
        try:
            return _open_meteo_answer(text, city)
        except Exception as exc:
            failures.append(("open-meteo", exc))
            problem("weather_provider_failed", type(exc).__name__, provider="open-meteo", tls=_tls_error(exc))
    try:
        return _wttr_answer(text, city)
    except Exception as exc:
        failures.append(("wttr.in", exc))
        problem("weather_provider_failed", type(exc).__name__, provider="wttr.in", tls=_tls_error(exc))
    if any(_tls_error(exc) for _, exc in failures):
        return (
            "Погода не получена: HTTPS-сертификат погодного сервиса не прошёл проверку. "
            "Проверьте доверенный корневой сертификат Windows/Python или укажите "
            "LESHY_WEATHER_CA_BUNDLE с доверенным PEM. Проверка TLS не отключается."
        )
    if not city:
        return "Не удалось автоматически определить город через интернет. Напишите, например: «Погода в Челябинске»."
    return f"Сейчас не удалось получить актуальную погоду для «{city}»: оба интернет-сервиса недоступны. Повторите запрос позже."
