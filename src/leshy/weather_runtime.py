from __future__ import annotations

"""Фактический прогноз погоды из Open-Meteo, без генерации цифр локальной LLM."""

import json
import os
import re
import ssl
from urllib import parse, request

_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
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
_NON_CITY = {"интернете", "сети", "окне", "городе", "этом", "помещении", "приложении"}


def is_weather_request(text: str) -> bool:
    """Вопрос о погоде — отдельно от температуры компьютера или модели."""
    q = " ".join(str(text or "").lower().split())
    return bool(re.search(r"\b(?:погод\w*|weather|прогноз\s+погод\w*)\b", q))


def _city(text: str) -> str:
    q = " ".join(str(text or "").strip().split())
    # Не захватываем всё до конца фразы: в запросе «посмотри в интернете
    # погоду в Челябинске» слово «интернете» не является городом.
    candidates = re.findall(
        r"\b(?:в|во)\s+([А-Яа-яЁёA-Za-z-]{2,48})",
        q, flags=re.IGNORECASE,
    )
    for candidate in reversed(candidates):
        city = candidate.strip()
        if city.lower() not in _NON_CITY:
            return _CITY_ALIASES.get(city.lower(), city)
    return os.getenv("LESHY_WEATHER_CITY", "").strip()[:80]


def _get_json(endpoint: str, params: dict[str, str]) -> dict:
    url = endpoint + "?" + parse.urlencode(params)
    req = request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Leshy-AI/Weather/1",
    })
    # Не отключаем проверку сертификатов TLS.
    with request.urlopen(req, timeout=9) as response:
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


def weather_answer(text: str) -> str | None:
    """Готовый ответ с источником; None только для запросов не о погоде."""
    if not is_weather_request(text):
        return None
    city = _city(text)
    if not city:
        return "В каком городе проверить погоду? Укажите город — я получу актуальный прогноз."
    try:
        geocoded = _get_json(_GEOCODING, {
            "name": city, "count": "1", "language": "ru", "format": "json",
        })
        matches = geocoded.get("results") or []
        if not matches:
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
        lines.append("Источник: Open-Meteo (прогноз по метеомоделям, не измерение на Вашем устройстве): https://open-meteo.com/en/docs")
        return "\n".join(lines)
    except ssl.SSLCertVerificationError:
        return "Не удалось проверить HTTPS-сертификат погодного сервиса. Прогноз не получен; исправьте доверенные сертификаты Windows/Python."
    except Exception as exc:
        # Не выдаём выдуманные числа при сбое доступа к API.
        return f"Сейчас не удалось получить проверенный прогноз для «{city}» ({type(exc).__name__}). Повторите запрос позже."
