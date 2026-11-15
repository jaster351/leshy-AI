from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
import threading
import time
from typing import Any, Callable

from .browser_skills import BrowserSkillDefinition, BrowserSkillUnavailable, HumanBrowserSkillRunner
from .diagnostics import event, problem
from .storage import utcnow


PARCELRADAR_SKILL = BrowserSkillDefinition(
    skill_id="parcel.parcelradar.browser.v1",
    name="ParcelRadar — бесплатное отслеживание без регистрации",
    start_url="https://parcelradar.app/",
    allowed_hosts=("parcelradar.app",),
    input_hints=("Enter tracking number", "Tracking number", "трек"),
    submit_hints=("Track", "Отследить", "Search"),
    result_markers=(
        "In transit",
        "Out for delivery",
        "Delivered",
        "Customs",
        "Carrier",
        "Tracking",
        "Not found",
        "Посылка",
        "Отправление",
        "трек",
    ),
)

_TRACK_RE = re.compile(r"(?<![A-Za-zА-Яа-я0-9])([A-Za-z0-9][A-Za-z0-9-]{7,39})(?![A-Za-zА-Яа-я0-9])")
_DATE_RE = re.compile(
    r"(?:\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b|"
    r"\b\d{1,2}:\d{2}(?::\d{2})?\b)",
    re.IGNORECASE,
)

_UI_ONLY = {
    "carrier", "status", "tracking", "tracking history", "track", "track parcel", "track a parcel",
    "where is my parcel?", "enter your tracking number", "tracking number", "search", "parcelradar",
    "перевозчик", "статус", "отслеживание", "история отслеживания", "номер отслеживания", "отследить",
}

_EVENT_WORDS = (
    "delivered", "received by", "out for delivery", "with courier", "ready for pickup", "ready for collection",
    "in transit", "arrived", "departed", "accepted", "picked up", "customs", "export", "import",
    "shipment received", "delivery failed", "return to sender", "exception",
    "достав", "вруч", "получ", "курьер", "готов к выдаче", "ожидает в отделении", "в пути", "сортиров",
    "прибыл", "покинул", "принят", "тамож", "экспорт", "импорт", "возврат", "неудачная попытка",
)


@dataclass(frozen=True, slots=True)
class ParcelIntent:
    action: str
    tracking_number: str = ""


@dataclass(frozen=True, slots=True)
class ParcelResult:
    tracking_number: str
    status: str
    status_category: str
    last_event_text: str
    source_url: str
    checked_at: str
    fingerprint: str
    excerpt: str
    browser_name: str = ""
    error: str = ""
    needs_user_action: bool = False


def _normalize_track(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", str(value or "")).upper().strip("-")[:40]


def extract_tracking_numbers(text: str) -> list[str]:
    values: list[str] = []
    for match in _TRACK_RE.finditer(str(text or "")):
        candidate = _normalize_track(match.group(1))
        digits = sum(ch.isdigit() for ch in candidate)
        if len(candidate) < 8 or digits < 5:
            continue
        if candidate not in values:
            values.append(candidate)
    return values[:8]


def parse_parcel_intent(text: str) -> ParcelIntent | None:
    raw = " ".join(str(text or "").split()).strip()
    lower = raw.lower().replace("ё", "е")
    tracks = extract_tracking_numbers(raw)

    if any(marker in lower for marker in ("покажи мои посыл", "мои посылки", "список посыл", "что я отслеживаю")):
        return ParcelIntent("list")

    track = tracks[0] if tracks else ""
    if track and any(marker in lower for marker in ("перестань следить", "не следи", "удали трек", "убери посыл", "отмени отслеж")):
        return ParcelIntent("stop", track)
    if track and any(marker in lower for marker in ("следи", "отслеживай", "сообщай", "уведомляй", "напиши когда", "проверяй")):
        return ParcelIntent("follow", track)

    parcel_markers = ("трек", "посыл", "отправлен", "достав", "где моя", "отслед")
    pure_track = bool(track and _normalize_track(raw) == track)
    if track and (pure_track or any(marker in lower for marker in parcel_markers)):
        return ParcelIntent("track", track)
    return None


def _clean_lines(text: str) -> list[str]:
    ignored = {
        "форум", "войти", "регистрация", "главная", "сервисы", "поддержка",
        "политика конфиденциальности", "пользовательское соглашение",
        "sign in", "sign up", "privacy policy", "terms of use", "cookie policy",
    }
    result: list[str] = []
    for value in str(text or "").splitlines():
        line = " ".join(value.split()).strip()
        if not line or line.lower() in ignored:
            continue
        result.append(line)
    return result


def _is_ui_line(line: str, track: str = "") -> bool:
    value = " ".join(str(line or "").split()).strip()
    lower = value.lower().replace("ё", "е").strip(" :—-·")
    if not lower:
        return True
    if track and _normalize_track(value) == _normalize_track(track):
        return True
    if lower in _UI_ONLY:
        return True
    generic = (
        "free parcel tracking", "track every parcel", "enter your tracking number", "finds the carrier",
        "delivery estimate", "full tracking history", "where is my parcel", "tracking number",
        "отслеживание почтов", "почтового отправления", "трек-код", "отследить посылку",
    )
    return any(marker in lower for marker in generic)


def _relevant_excerpt(text: str, track: str) -> list[str]:
    lines = _clean_lines(text)
    upper_track = track.upper()
    indexes = [idx for idx, line in enumerate(lines) if upper_track in line.upper()]
    if indexes:
        start = max(0, indexes[0] - 5)
        return lines[start:start + 90]
    keywords = _EVENT_WORDS + (
        "не найден", "нет информации", "not found", "no information", "tracking", "carrier", "status",
    )
    selected = [line for line in lines if any(word in line.lower() for word in keywords)]
    return selected[:90] if selected else lines[:70]


def _category(text: str) -> str:
    value = text.lower().replace("ё", "е")
    if any(marker in value for marker in (
        "доставлен", "доставлено", "вручено", "вручен", "получено получателем", "получен адресатом",
        "delivered", "delivered to recipient", "received by recipient",
    )):
        return "delivered"
    if any(marker in value for marker in (
        "передан курьеру", "курьер получил", "доставка курьером", "готов к выдаче", "ожидает в отделении", "прибыло в место вручения",
        "out for delivery", "with courier", "ready for pickup", "ready for collection",
    )):
        return "out_for_delivery"
    if any(marker in value for marker in (
        "возврат", "неудачная попытка", "утерян", "задержан", "проблем", "исключение", "не вручено",
        "return to sender", "delivery failed", "failed delivery", "lost", "exception", "delivery exception",
    )):
        return "exception"
    if any(marker in value for marker in (
        "в пути", "сортиров", "прибыл", "покинул", "принят", "экспорт", "импорт", "отправлено", "отправлен из", "тамож",
        "in transit", "arrived", "departed", "accepted", "picked up", "customs", "export", "import", "shipment received",
    )):
        return "in_transit"
    return "unknown"


def _event_text(lines: list[str], category: str, track: str) -> str:
    """Выделить реальное последнее событие, но никогда не принимать UI-ярлыки вроде Carrier за статус."""
    best_index = -1
    best_score = -1
    category_words = {
        "delivered": ("достав", "вруч", "получ", "delivered", "received by"),
        "out_for_delivery": ("курьер", "готов к выдаче", "место вручения", "отделени", "out for delivery", "with courier", "ready for"),
        "exception": ("возврат", "неудач", "утерян", "задерж", "проблем", "return", "failed", "lost", "exception"),
        "in_transit": ("сортиров", "прибыл", "покинул", "принят", "экспорт", "импорт", "тамож", "в пути", "отправлено", "отправлен из", "in transit", "arrived", "departed", "accepted", "customs", "export", "import"),
    }
    preferred = category_words.get(category, _EVENT_WORDS)
    for index, line in enumerate(lines):
        if _is_ui_line(line, track):
            continue
        lower = line.lower().replace("ё", "е")
        score = 0
        if any(word in lower for word in preferred):
            score += 8
        elif any(word in lower for word in _EVENT_WORDS):
            score += 4
        if _DATE_RE.search(line):
            score += 2
        if any(ch.isdigit() for ch in line) and any(token in lower for token in ("мск", "utc", "gmt", "km", "км")):
            score += 1
        if score > best_score:
            best_score = score
            best_index = index

    if best_index < 0 or best_score <= 0:
        return ""

    parts = [lines[best_index]]
    for candidate in lines[best_index + 1:best_index + 4]:
        if _is_ui_line(candidate, track):
            continue
        lower = candidate.lower().replace("ё", "е")
        if any(word in lower for word in _EVENT_WORDS) and parts:
            break
        if len(candidate) <= 180:
            parts.append(candidate)
        if len(parts) >= 3:
            break
    return " — ".join(dict.fromkeys(part.strip() for part in parts if part.strip()))[:700]


def _human_status(category: str, event_text: str) -> str:
    labels = {
        "delivered": "Посылка доставлена",
        "out_for_delivery": "Посылка передана на доставку или готова к выдаче",
        "exception": "У посылки возникла проблема с доставкой",
        "in_transit": "Посылка в пути",
    }
    label = labels.get(category, "")
    if label and event_text:
        lower_event = event_text.lower().replace("ё", "е")
        if lower_event.strip(" .:-") not in {"delivered", "in transit", "out for delivery", "carrier", "status", "tracking"}:
            return f"{label}. Последнее событие: {event_text}"
        return label
    if label:
        return label
    if event_text:
        return f"Последнее событие: {event_text}"
    return "Сервис не показал подтверждённый текущий статус или местоположение посылки."


def parse_parcel_page(track: str, visible_text: str, source_url: str, *, browser_name: str = "") -> ParcelResult:
    checked = utcnow()
    lines = _relevant_excerpt(visible_text, track)
    excerpt = "\n".join(lines[:60])[:5000]
    lower = excerpt.lower().replace("ё", "е")
    not_found = any(marker in lower for marker in (
        "не найден", "ничего не найдено", "нет информации", "информация об отправлении не найдена",
        "пока нет данных", "не удалось найти отправление", "not found", "no information", "no tracking information",
        "shipment not found", "tracking number not found",
    ))
    if not_found:
        category = "unknown"
        event_text = ""
        status = "Сведения по этому трек-номеру пока не найдены."
    else:
        category = _category(excerpt)
        event_text = _event_text(lines, category, track)
        status = _human_status(category, event_text)

    stable = f"{category}\n{status}\n{event_text}".lower()
    fingerprint = sha256(stable.encode("utf-8", errors="ignore")).hexdigest()
    return ParcelResult(
        tracking_number=track,
        status=status,
        status_category=category,
        last_event_text=event_text or status,
        source_url=source_url,
        checked_at=checked,
        fingerprint=fingerprint,
        excerpt=excerpt,
        browser_name=browser_name,
    )


class ParcelTrackingService:
    """Отслеживание отправлений через browser-skills с подтверждением подписки пользователем."""

    def __init__(self, app, runner: HumanBrowserSkillRunner | None = None):
        self.app = app
        self.storage = app.storage
        self.runner = runner or HumanBrowserSkillRunner()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.storage.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS parcel_subscriptions("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id TEXT NOT NULL,tracking_number TEXT NOT NULL,"
                "site TEXT NOT NULL DEFAULT 'parcelradar',status TEXT NOT NULL DEFAULT '',status_category TEXT NOT NULL DEFAULT 'unknown',"
                "last_event_text TEXT NOT NULL DEFAULT '',fingerprint TEXT NOT NULL DEFAULT '',source_url TEXT NOT NULL DEFAULT '',"
                "last_checked_at TEXT,last_change_at TEXT,next_check_epoch REAL NOT NULL DEFAULT 0,consecutive_failures INTEGER NOT NULL DEFAULT 0,"
                "state TEXT NOT NULL DEFAULT 'active',notify_channel TEXT NOT NULL DEFAULT 'web',notify_target TEXT NOT NULL DEFAULT '',"
                "session_id TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
                "UNIQUE(owner_id,tracking_number))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS parcel_events("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,subscription_id INTEGER NOT NULL REFERENCES parcel_subscriptions(id) ON DELETE CASCADE,"
                "fingerprint TEXT NOT NULL,status TEXT NOT NULL,status_category TEXT NOT NULL,source_url TEXT NOT NULL,checked_at TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_parcel_events_subscription ON parcel_events(subscription_id,id DESC)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS parcel_notifications("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id TEXT NOT NULL,tracking_number TEXT NOT NULL,text TEXT NOT NULL,"
                "channel TEXT NOT NULL,target TEXT NOT NULL,session_id TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,delivered_at TEXT)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_parcel_due ON parcel_subscriptions(state,next_check_epoch)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_parcel_notifications_pending ON parcel_notifications(delivered_at,id)")

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="leshy-parcel-monitor", daemon=True)
            self._thread.start()
            event("parcel_monitor_started")

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        event("parcel_monitor_stopped")

    def track_once(self, tracking_number: str, *, activity: Callable[[str, dict], None] | None = None) -> ParcelResult:
        track = _normalize_track(tracking_number)
        if not track:
            raise ValueError("Трек-номер не указан")
        execution = self.runner.run_form_skill(PARCELRADAR_SKILL, track, activity=activity)
        if execution.status == "needs_user_action":
            return ParcelResult(
                tracking_number=track,
                status="Сайт требует действия пользователя в браузере.",
                status_category="unknown",
                last_event_text="",
                source_url=execution.page_url,
                checked_at=utcnow(),
                fingerprint="",
                excerpt=execution.visible_text[:2000],
                browser_name=execution.browser_name,
                needs_user_action=True,
            )
        return parse_parcel_page(track, execution.visible_text, execution.page_url, browser_name=execution.browser_name)

    @staticmethod
    def _next_interval(category: str, *, failures: int = 0) -> float:
        if failures:
            return min(6 * 3600.0, 15 * 60.0 * (2 ** min(failures, 5)))
        if category == "out_for_delivery":
            return 45 * 60.0
        if category == "exception":
            return 60 * 60.0
        if category == "delivered":
            return 0.0
        return 2 * 3600.0

    def offer_follow(
        self,
        owner_id: str,
        result: ParcelResult,
        *,
        notify_channel: str,
        notify_target: str,
        session_id: str,
    ) -> bool:
        if result.needs_user_action or result.status_category == "delivered":
            self.decline_pending(owner_id, result.tracking_number)
            return False
        now = utcnow()
        expires = time.time() + 24 * 3600.0
        with self.storage.connection() as conn:
            conn.execute(
                "DELETE FROM parcel_subscriptions WHERE owner_id=? AND state='awaiting_confirmation' AND tracking_number<>?",
                (owner_id, result.tracking_number),
            )
            conn.execute(
                "INSERT INTO parcel_subscriptions("
                "owner_id,tracking_number,status,status_category,last_event_text,fingerprint,source_url,last_checked_at,last_change_at,"
                "next_check_epoch,consecutive_failures,state,notify_channel,notify_target,session_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(owner_id,tracking_number) DO UPDATE SET "
                "status=excluded.status,status_category=excluded.status_category,last_event_text=excluded.last_event_text,"
                "fingerprint=excluded.fingerprint,source_url=excluded.source_url,last_checked_at=excluded.last_checked_at,"
                "next_check_epoch=excluded.next_check_epoch,consecutive_failures=0,state=CASE WHEN parcel_subscriptions.state='active' THEN 'active' ELSE excluded.state END,"
                "notify_channel=excluded.notify_channel,notify_target=excluded.notify_target,session_id=excluded.session_id,updated_at=excluded.updated_at",
                (
                    owner_id, result.tracking_number, result.status, result.status_category, result.last_event_text,
                    result.fingerprint, result.source_url, result.checked_at, result.checked_at, expires, 0,
                    "awaiting_confirmation", notify_channel, notify_target, session_id or "", now, now,
                ),
            )
        return True

    def pending_follow(self, owner_id: str, session_id: str | None = None) -> dict[str, Any] | None:
        now_epoch = time.time()
        with self.storage.connection() as conn:
            conn.execute(
                "DELETE FROM parcel_subscriptions WHERE state='awaiting_confirmation' AND next_check_epoch>0 AND next_check_epoch<?",
                (now_epoch,),
            )
            if session_id:
                row = conn.execute(
                    "SELECT * FROM parcel_subscriptions WHERE owner_id=? AND session_id=? AND state='awaiting_confirmation' "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (owner_id, session_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM parcel_subscriptions WHERE owner_id=? AND state='awaiting_confirmation' ORDER BY updated_at DESC LIMIT 1",
                    (owner_id,),
                ).fetchone()
        return dict(row) if row is not None else None

    def confirm_pending(self, owner_id: str, tracking_number: str) -> dict[str, Any] | None:
        track = _normalize_track(tracking_number)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT * FROM parcel_subscriptions WHERE owner_id=? AND tracking_number=? AND state='awaiting_confirmation'",
                (owner_id, track),
            ).fetchone()
            if row is None:
                return None
            category = str(row["status_category"] or "unknown")
            next_epoch = time.time() + self._next_interval(category)
            conn.execute(
                "UPDATE parcel_subscriptions SET state='active',next_check_epoch=?,updated_at=? WHERE id=?",
                (next_epoch, utcnow(), int(row["id"])),
            )
            updated = dict(row)
            updated["state"] = "active"
            updated["next_check_epoch"] = next_epoch
        self.start()
        event("parcel_following_enabled", owner_id=owner_id, tracking_number=track, channel=str(updated.get("notify_channel") or ""))
        return updated

    def decline_pending(self, owner_id: str, tracking_number: str = "") -> bool:
        track = _normalize_track(tracking_number)
        with self.storage.connection() as conn:
            if track:
                cur = conn.execute(
                    "DELETE FROM parcel_subscriptions WHERE owner_id=? AND tracking_number=? AND state='awaiting_confirmation'",
                    (owner_id, track),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM parcel_subscriptions WHERE owner_id=? AND state='awaiting_confirmation'",
                    (owner_id,),
                )
        return bool(cur.rowcount)

    def follow(
        self,
        owner_id: str,
        tracking_number: str,
        *,
        notify_channel: str,
        notify_target: str,
        session_id: str = "",
        activity: Callable[[str, dict], None] | None = None,
    ) -> ParcelResult:
        result = self.track_once(tracking_number, activity=activity)
        if result.status_category == "delivered":
            with self.storage.connection() as conn:
                conn.execute("DELETE FROM parcel_subscriptions WHERE owner_id=? AND tracking_number=?", (owner_id, result.tracking_number))
            return result
        now = utcnow()
        state = "needs_user_action" if result.needs_user_action else "active"
        next_epoch = 0.0 if state != "active" else time.time() + self._next_interval(result.status_category)
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO parcel_subscriptions("
                "owner_id,tracking_number,status,status_category,last_event_text,fingerprint,source_url,last_checked_at,last_change_at,"
                "next_check_epoch,consecutive_failures,state,notify_channel,notify_target,session_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(owner_id,tracking_number) DO UPDATE SET "
                "status=excluded.status,status_category=excluded.status_category,last_event_text=excluded.last_event_text,"
                "fingerprint=excluded.fingerprint,source_url=excluded.source_url,last_checked_at=excluded.last_checked_at,"
                "next_check_epoch=excluded.next_check_epoch,consecutive_failures=0,state=excluded.state,"
                "notify_channel=excluded.notify_channel,notify_target=excluded.notify_target,session_id=excluded.session_id,updated_at=excluded.updated_at",
                (
                    owner_id, result.tracking_number, result.status, result.status_category, result.last_event_text,
                    result.fingerprint, result.source_url, result.checked_at, result.checked_at, next_epoch, 0, state,
                    notify_channel, notify_target, session_id or "", now, now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM parcel_subscriptions WHERE owner_id=? AND tracking_number=?",
                (owner_id, result.tracking_number),
            ).fetchone()
            if row is not None and result.fingerprint:
                conn.execute(
                    "INSERT INTO parcel_events(subscription_id,fingerprint,status,status_category,source_url,checked_at) VALUES(?,?,?,?,?,?)",
                    (int(row["id"]), result.fingerprint, result.status, result.status_category, result.source_url, result.checked_at),
                )
        self.start()
        event("parcel_following_enabled", owner_id=owner_id, tracking_number=result.tracking_number, channel=notify_channel)
        return result

    def stop_following(self, owner_id: str, tracking_number: str) -> bool:
        track = _normalize_track(tracking_number)
        with self.storage.connection() as conn:
            cur = conn.execute(
                "DELETE FROM parcel_subscriptions WHERE owner_id=? AND tracking_number=?",
                (owner_id, track),
            )
        changed = bool(cur.rowcount)
        if changed:
            event("parcel_following_stopped", owner_id=owner_id, tracking_number=track)
        return changed

    def list(self, owner_id: str, *, include_delivered: bool = True) -> list[dict[str, Any]]:
        query = (
            "SELECT tracking_number,status,status_category,last_event_text,source_url,last_checked_at,state,"
            "notify_channel,session_id,created_at,updated_at FROM parcel_subscriptions "
            "WHERE owner_id=? AND state<>'awaiting_confirmation'"
        )
        params: list[Any] = [owner_id]
        if not include_delivered:
            query += " AND state<>'delivered'"
        query += " ORDER BY updated_at DESC"
        with self.storage.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _queue_notification(self, row, result: ParcelResult) -> None:
        if result.status_category == "delivered":
            text = f"Посылка {result.tracking_number} доставлена. Удаляю её из отслеживания."
        else:
            text = f"Посылка {result.tracking_number}: статус изменился — {result.status}"
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO parcel_notifications(owner_id,tracking_number,text,channel,target,session_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    str(row["owner_id"]), result.tracking_number, text, str(row["notify_channel"]),
                    str(row["notify_target"]), str(row["session_id"]), utcnow(),
                ),
            )
        event("parcel_status_changed", owner_id=str(row["owner_id"]), tracking_number=result.tracking_number, status=result.status)

    def _check_row(self, row) -> None:
        subscription_id = int(row["id"])
        previous = str(row["fingerprint"] or "")
        previous_category = str(row["status_category"] or "unknown")
        try:
            result = self.track_once(str(row["tracking_number"]))
            if result.needs_user_action:
                with self.storage.connection() as conn:
                    conn.execute(
                        "UPDATE parcel_subscriptions SET state='needs_user_action',next_check_epoch=0,last_checked_at=?,updated_at=? WHERE id=?",
                        (result.checked_at, utcnow(), subscription_id),
                    )
                return

            changed = bool(previous and result.fingerprint and result.fingerprint != previous)
            delivered_now = result.status_category == "delivered"
            if delivered_now:
                if changed or previous_category != "delivered":
                    self._queue_notification(row, result)
                with self.storage.connection() as conn:
                    conn.execute("DELETE FROM parcel_subscriptions WHERE id=?", (subscription_id,))
                event("parcel_following_completed", owner_id=str(row["owner_id"]), tracking_number=result.tracking_number)
                return

            next_epoch = time.time() + self._next_interval(result.status_category)
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE parcel_subscriptions SET status=?,status_category=?,last_event_text=?,fingerprint=?,source_url=?,"
                    "last_checked_at=?,last_change_at=CASE WHEN ? THEN ? ELSE last_change_at END,next_check_epoch=?,"
                    "consecutive_failures=0,state='active',updated_at=? WHERE id=?",
                    (
                        result.status, result.status_category, result.last_event_text, result.fingerprint, result.source_url,
                        result.checked_at, 1 if changed else 0, result.checked_at, next_epoch, utcnow(), subscription_id,
                    ),
                )
                if result.fingerprint and result.fingerprint != previous:
                    conn.execute(
                        "INSERT INTO parcel_events(subscription_id,fingerprint,status,status_category,source_url,checked_at) VALUES(?,?,?,?,?,?)",
                        (subscription_id, result.fingerprint, result.status, result.status_category, result.source_url, result.checked_at),
                    )
            if changed:
                self._queue_notification(row, result)
        except Exception as exc:
            failures = int(row["consecutive_failures"] or 0) + 1
            next_epoch = time.time() + self._next_interval("unknown", failures=failures)
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE parcel_subscriptions SET consecutive_failures=?,next_check_epoch=?,updated_at=? WHERE id=?",
                    (failures, next_epoch, utcnow(), subscription_id),
                )
            problem("parcel_check_failed", str(exc), tracking_number=str(row["tracking_number"]), failures=failures)

    def check_due(self, *, limit: int = 3) -> int:
        now_epoch = time.time()
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM parcel_subscriptions WHERE state='active' AND next_check_epoch>0 AND next_check_epoch<=? "
                "ORDER BY next_check_epoch ASC LIMIT ?",
                (now_epoch, max(1, min(int(limit), 20))),
            ).fetchall()
        for row in rows:
            if self._stop.is_set():
                break
            self._check_row(row)
        self.deliver_pending(limit=10)
        return len(rows)

    def deliver_pending(self, *, limit: int = 10) -> int:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM parcel_notifications WHERE delivered_at IS NULL ORDER BY id ASC LIMIT ?",
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        delivered = 0
        for row in rows:
            channel = str(row["channel"] or "web")
            text = str(row["text"] or "")
            try:
                if channel == "telegram":
                    telegram = getattr(self.app, "telegram", None)
                    sender = getattr(telegram, "_send", None)
                    if not callable(sender):
                        continue
                    target = str(row["target"] or "").strip()
                    if not target:
                        continue
                    sender(int(target), text)
                else:
                    session_id = str(row["session_id"] or "").strip()
                    if not session_id:
                        continue
                    self.app._save_assistant(session_id, text)
                with self.storage.connection() as conn:
                    conn.execute("UPDATE parcel_notifications SET delivered_at=? WHERE id=?", (utcnow(), int(row["id"])))
                delivered += 1
            except Exception as exc:
                problem("parcel_notification_failed", str(exc), notification_id=int(row["id"]), channel=channel)
        return delivered

    def _run(self) -> None:
        while not self._stop.wait(30.0):
            try:
                self.check_due(limit=3)
            except Exception as exc:
                problem("parcel_monitor_cycle_failed", str(exc))


def format_parcel_result(result: ParcelResult, *, following: bool = False, offer_follow: bool = False) -> str:
    if result.needs_user_action:
        return (
            f"Для трека {result.tracking_number} сайт запросил действие пользователя в браузере "
            f"(например, CAPTCHA/challenge). После её прохождения я смогу продолжить проверку."
        )
    lines = [f"{result.tracking_number}: {result.status}"]
    if following:
        if result.status_category == "delivered":
            lines.append("Посылка уже доставлена, поэтому постоянное отслеживание не требуется.")
        else:
            lines.append("Хорошо, слежу за этой посылкой. Сообщу, когда статус изменится, и удалю её из отслеживания после доставки.")
    elif offer_follow and result.status_category != "delivered":
        lines.append("Хотите, чтобы я следил за этой посылкой и сообщил, когда статус изменится?")
    return "\n".join(lines)


def format_parcel_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return "У Вас пока нет посылок на отслеживании."
    lines = ["Посылки, за которыми я слежу:"]
    for item in items[:30]:
        lines.append(f"- {item.get('tracking_number')}: {item.get('status') or 'статус пока неизвестен'}")
    return "\n".join(lines)


__all__ = [
    "PARCELRADAR_SKILL",
    "ParcelIntent",
    "ParcelResult",
    "ParcelTrackingService",
    "BrowserSkillUnavailable",
    "extract_tracking_numbers",
    "parse_parcel_intent",
    "parse_parcel_page",
    "format_parcel_result",
    "format_parcel_list",
]
