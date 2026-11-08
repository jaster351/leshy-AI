from __future__ import annotations

"""Context-aware, source-backed cinema queries for Web and Telegram chats."""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import re
from typing import Any


# Не считать любой вопрос о ФИЛЬМЕ запросом киноафиши. Например:
# «Помоги найти фильм Одиссея Нолана» — поиск фильма, не сеансов на сегодня.
_CINEMA_VENUE = re.compile(
    r"кинотеатр|киноафиш|\bкино\b|мягк(?:ий|ого|ом)\s+кинотеатр|"
    r"\bт[рг]ц\s+алмаз\b",
    re.IGNORECASE,
)
_SCREENING_INTENT = re.compile(r"сеанс\w*|афиш\w*|показыва\w*|билет\w*", re.IGNORECASE)
_FILM_TOPIC = re.compile(r"фильм\w*|\bкино\b", re.IGNORECASE)
_FOLLOWUP = re.compile(
    r"что (?:там )?нового|что показыва|что ид[её]т|что скоро|"
    r"а (?:что|когда|сколько)|сколько стоит|есть ли места|"
    r"билет|на какое время|что выйдет",
    re.IGNORECASE,
)
_UPCOMING = re.compile(r"скоро|премьер|будет|выйд|в будущем|ожида", re.IGNORECASE)
_NOW = re.compile(r"сейчас|сегодня|завтра|ид[её]т|показыва|сеанс|афиш|расписан", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CinemaQuery:
    venue: str
    city: str
    day: str
    now: bool
    upcoming: bool

    @property
    def label(self) -> str:
        return " ".join(part for part in (self.venue, self.city) if part).strip() or "кино"


def _message_history(rows: Any) -> list[str]:
    """Only the current session's preceding user messages may supply context."""
    result: list[str] = []
    for row in rows or ():
        try:
            role, content = str(row["role"]), str(row["content"])
        except (KeyError, TypeError, IndexError):
            continue
        if role == "user" and content.strip():
            result.append(content.strip())
    return result


def cinema_query(message: str, rows: Any = (), *, day: str | None = None) -> CinemaQuery | None:
    value = " ".join(str(message or "").split())
    if not value:
        return None
    history = _message_history(rows)
    previous = next(
        (entry for entry in history if entry != value and _CINEMA_VENUE.search(entry)),
        "",
    )
    direct = bool(
        _CINEMA_VENUE.search(value)
        or (_SCREENING_INTENT.search(value) and _FILM_TOPIC.search(value))
    )
    if not direct and not (_FOLLOWUP.search(value) and previous):
        return None

    # The latest explicit user message overrides older context.
    context = value if direct else previous + " " + value
    city_re = r"Челябинск\w*|Екатеринбург\w*|Москв\w*|Санкт-Петербург\w*"
    city_match = re.search(city_re, context, re.IGNORECASE)
    if city_match is None and direct and previous:
        city_match = re.search(city_re, previous, re.IGNORECASE)
    city = ""
    if city_match:
        name = city_match.group(0).lower()
        city = (
            "Челябинск" if name.startswith("челябин") else
            "Екатеринбург" if name.startswith("екатерин") else
            "Москва" if name.startswith("москв") else "Санкт-Петербург"
        )

    venue = ""
    if re.search(r"\bалмаз\b", context, re.IGNORECASE):
        venue = "Мягкий кинотеатр Алмаз"
        if not city:
            city = "Челябинск" if "мягк" in context.lower() else ""
    elif re.search(r"\bфокус\b", context, re.IGNORECASE):
        venue = "Мягкий кинотеатр Фокус"
    elif re.search(r"\bмегаполис\b", context, re.IGNORECASE):
        venue = "Мягкий кинотеатр Мегаполис"
    elif re.search(r"мягк\w*\s+кинотеатр", context, re.IGNORECASE):
        venue = "Мягкий кинотеатр"

    wants_upcoming = bool(_UPCOMING.search(value))
    wants_now = bool(_NOW.search(value))
    # A general 'what's new?' about a cinema means both current titles and coming soon.
    general = bool(re.search(r"что\s+(?:там\s+)?нового|новинк|новые фильмы", value, re.IGNORECASE))
    if general or (not wants_now and not wants_upcoming):
        wants_now = True
        wants_upcoming = True
    if city == "Челябинск":
        today = datetime.now(timezone(timedelta(hours=5))).date()
    elif city == "Москва":
        today = datetime.now(timezone(timedelta(hours=3))).date()
    else:
        today = datetime.now().astimezone().date()
    return CinemaQuery(venue, city, day or today.isoformat(), wants_now, wants_upcoming)


def cinema_queries(query: CinemaQuery) -> tuple[str, ...]:
    searches: list[str] = []
    if query.now:
        searches.append(
            f"{query.label} расписание сеансов афиша {query.day} цены билеты"
        )
    if query.upcoming:
        searches.append(
            f"{query.label} скоро в кино ближайшие премьеры даты выхода после {query.day}"
        )
    return tuple(searches)


def cinema_instruction(query: CinemaQuery) -> str:
    return (
        "ЗАПРОС О КИНОТЕАТРЕ. Используй только результаты актуального поиска ниже, "
        "а не догадки и старую память. Уточнённый объект: " + query.label + ". "
        "Дата проверки: " + query.day + ". "
        "В ответе выдели две части при наличии запроса: «Сейчас показывают» и «Скоро выйдет». "
        "Для текущих фильмов назови РЕАЛЬНЫЕ названия, дату и время сеансов, краткое описание "
        "и цену билета, только если эти значения присутствуют в источнике. "
        "Для будущих премьер укажи названия, проверенную дату выхода и краткое описание. "
        "Если есть достоверная ссылка на постер, её можно дать; выдумывать изображения нельзя. "
        "Свободные места можно подтвердить ТОЛЬКО по актуальной схеме выбранного сеанса. "
        "Продажа билетов или цена сами по себе не подтверждают свободные места. "
        "Не называй фильм будущим показом именно в этом кинотеатре, если он присутствует "
        "только в общегородском календаре премьер, но отсутствует в афише кинотеатра. "
        "Указывай источник и различай дату публикации страницы и дату сеанса. "
        "Если фактов о конкретных сеансах либо премьерах не найдено, прямо скажи "
        "об отсутствии подтверждённых данных, не предлагай пользователю искать всё самостоятельно. "
        "Никогда не придумывай фильм, время, дату премьеры, цену или занятость зала."
    )


def cinema_search(app: Any, query: CinemaQuery) -> dict[str, Any]:
    """Execute independent live searches; partial failures never invent a result."""
    results: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for text in cinema_queries(query):
        try:
            response = app.live_search(text, limit=8, reason="актуальная афиша и будущие премьеры")
            if str(response.get("status") or "") == "search_failed":
                raise RuntimeError(str(response.get("error") or "поиск недоступен"))
            for item in response.get("results") or ():
                title = str(item.get("title") or "").strip()
                url = str(item.get("url") or "").strip()
                snippet = str(item.get("snippet") or "").strip()
                key = (url, title)
                if url and key not in seen:
                    results.append({"title": title, "url": url, "snippet": snippet})
                    seen.add(key)
            if response.get("error"):
                errors.append(str(response["error"]))
        except Exception as exc:
            errors.append(str(exc))
    return {"results": results, "error": "; ".join(errors) if errors else ""}


def cinema_no_data(query: CinemaQuery) -> str:
    """A deterministic truthful result when all live lookups are empty or failed."""
    return (
        f"Не удалось получить подтверждённые сведения об афише и ближайших премьерах "
        f"для «{query.label}» на {query.day}. "
        "Я не буду придумывать названия фильмов, время сеансов, цены или свободные места. "
        "Повторите запрос позже: источник расписания может быть временно недоступен."
    )
