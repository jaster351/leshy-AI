from __future__ import annotations

import re
from typing import Any


def _clean_status(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    generic = (
        "статус на странице найден",
        "status found",
        "tracking number",
        "track every parcel",
        "free parcel tracking",
    )
    if any(marker in text.lower() for marker in generic):
        return ""
    return text[:700]


def _best_event(result: Any) -> str:
    direct = _clean_status(getattr(result, "last_event_text", "")) or _clean_status(getattr(result, "status", ""))
    if direct:
        return direct

    excerpt = str(getattr(result, "excerpt", "") or "")
    lines = [" ".join(line.split()).strip() for line in excerpt.splitlines()]
    lines = [line for line in lines if line]
    keywords = (
        "arrived", "departed", "accepted", "customs", "transit", "delivery", "delivered", "pickup",
        "прибыл", "покинул", "принят", "тамож", "сортиров", "достав", "вручен", "выдач", "курьер",
    )
    for line in lines:
        low = line.lower().replace("ё", "е")
        if any(word in low for word in keywords):
            return line[:700]
    return ""


def format_parcel_result_short(result: Any, *, following: bool = False) -> str:
    track = str(getattr(result, "tracking_number", "") or "").strip()
    if bool(getattr(result, "needs_user_action", False)):
        return f"{track}: сайт запросил CAPTCHA/подтверждение пользователя. После прохождения проверки повторю отслеживание."

    category = str(getattr(result, "status_category", "unknown") or "unknown")
    event = _best_event(result)

    if not event:
        text = f"{track}: точное местоположение посылки сейчас определить не удалось."
    elif category == "delivered":
        text = f"{track}: посылка доставлена. {event}"
    elif category == "out_for_delivery":
        text = f"{track}: посылка уже на этапе выдачи/доставки. {event}"
    elif category == "exception":
        text = f"{track}: у посылки возникла проблема. {event}"
    elif category == "in_transit":
        text = f"{track}: посылка в пути. Последнее событие: {event}"
    else:
        text = f"{track}: {event}"

    if following:
        text += "\nБуду следить дальше и сообщу при изменении статуса."
    return text


def install_parcel_presenter() -> None:
    """Заменить техническое представление результата на короткий ответ ассистента."""
    from . import parcel_tracking
    from . import parcel_tracking_runtime

    parcel_tracking.format_parcel_result = format_parcel_result_short
    parcel_tracking_runtime.format_parcel_result = format_parcel_result_short


__all__ = ["format_parcel_result_short", "install_parcel_presenter"]
