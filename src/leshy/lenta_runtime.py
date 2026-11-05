from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Iterator

from . import browser_platform_runtime as browser_runtime
from .browser_agent import BrowserAction, BrowserRisk
from .browser_platform_runtime import BrowserPlatform
from .diagnostics import event, problem
from .lenta_browser_session import LentaBrowserSession, LentaSessionStore
from .lenta_module import LentaModule
from .module_settings import module_enabled
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow


_LENTA_URL = "https://lenta.com/"
_STATE_TTL_SECONDS = 30 * 60
_INSTALLED = False
_ORIGINAL_CHAT = None
_ORIGINAL_STREAM = None
_ORIGINAL_BROWSER_INTENT_PARSER = None

_YES = {
    "да", "давай", "оформляй", "оформи", "заказывай", "закажи", "подтверждаю",
    "подтвердить", "согласен", "согласна", "можно", "все верно", "всё верно",
    "да оформляй", "да заказывай", "ок", "окей",
}
_NO = {
    "нет", "не надо", "не нужно", "отмена", "отмени", "не оформляй", "не заказывай",
    "пока не надо", "оставь как есть",
}
_LOGIN_DONE = {
    "готово", "вошел", "вошёл", "вошла", "я вошел", "я вошёл", "я вошла",
    "авторизовался", "авторизовалась", "залогинился", "залогинилась",
}
_MANUAL = (
    "сам оплачу", "оплачу сам", "я сам оплачу", "сама оплачу", "сам оформлю",
    "сама оформлю", "я сам", "я сама", "дальше сам", "дальше сама", "оставь в корзине",
)
_MODIFY = (
    "убери", "удали", "добавь", "замени", "поменяй", "измени", "количество", "штук",
    "самовывоз", "доставка", "наличными", "картой", "при получении", "онлайн", "адрес",
)
_ORDER = ("закаж", "оформи заказ", "оформить заказ", "купи", "купить")
_CART = ("корзин", "собери", "спланируй", "спроектируй", "положи", "добавь товары", "добавь продукты")
_PRICE = ("сколько стоит", "цена", "цены", "стоимость", "наличие", "найди товар", "поищи товар", "найди продукт")
_CAPABILITY = ("умеешь", "можешь", "работаешь", "что умеешь", "можно ли")

_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS lenta_conversation_state(
    owner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    expires_at REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, session_id)
);
CREATE INDEX IF NOT EXISTS ix_lenta_state_expiry ON lenta_conversation_state(expires_at);
"""


@dataclass(frozen=True, slots=True)
class LentaIntent:
    kind: str
    text: str
    payment: str = ""


def _normalize(text: str) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9:/._\-\s]+", " ", value, flags=re.IGNORECASE)
    return " ".join(value.split()).strip()


def _explicit_lenta(text: str) -> bool:
    low = _normalize(text)
    if "lenta.com" in low or "лента.com" in low:
        return True
    return bool(re.search(r"\bлент(?:а|е|у|ы|ой)\b", low)) and any(
        marker in low
        for marker in (
            "магазин", "товар", "продукт", "корзин", "цен", "стоит", "заказ", "достав",
            "самовывоз", "куп", "оформ", "личн", "кабинет", "оплат", "налич",
        )
    )


def _payment(text: str) -> str:
    low = _normalize(text)
    if any(marker in low for marker in _MANUAL):
        return "self"
    if "налич" in low:
        return "cash"
    if "карт" in low and "при получ" in low:
        return "card_on_delivery"
    if "карт" in low and any(marker in low for marker in ("онлайн", "сейчас", "сразу", "оплат")):
        return "card_online"
    return ""


def _parse_explicit_intent(text: str) -> LentaIntent | None:
    if not _explicit_lenta(text):
        return None
    low = _normalize(text)
    payment = _payment(text)
    if any(marker in low for marker in _CAPABILITY):
        return LentaIntent("capability", text, payment)
    if any(marker in low for marker in ("войти", "авториз", "личный кабинет", "личном кабинете")) and not any(
        marker in low for marker in _ORDER
    ):
        return LentaIntent("login", text, payment)
    if any(marker in low for marker in _ORDER):
        return LentaIntent("checkout", text, payment)
    if any(marker in low for marker in _CART):
        return LentaIntent("basket", text, payment)
    if any(marker in low for marker in _PRICE):
        return LentaIntent("catalog", text, payment)
    return LentaIntent("catalog", text, payment)


def _ensure_schema(app) -> None:
    with app.storage.connection() as conn:
        conn.executescript(_STATE_SCHEMA)
        conn.execute("DELETE FROM lenta_conversation_state WHERE expires_at<?", (time.time(),))


def _load_state(app, owner_id: str, session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    _ensure_schema(app)
    with app.storage.connection() as conn:
        row = conn.execute(
            "SELECT status,payload_json,expires_at FROM lenta_conversation_state WHERE owner_id=? AND session_id=?",
            (owner_id, session_id),
        ).fetchone()
    if row is None:
        return None
    if float(row["expires_at"] or 0) <= time.time():
        _clear_state(app, owner_id, session_id)
        return None
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {"status": str(row["status"]), "payload": payload, "expires_at": float(row["expires_at"])}


def _save_state(app, owner_id: str, session_id: str, status: str, payload: dict[str, Any]) -> None:
    _ensure_schema(app)
    with app.storage.connection() as conn:
        conn.execute(
            """INSERT INTO lenta_conversation_state(owner_id,session_id,status,payload_json,expires_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(owner_id,session_id) DO UPDATE SET
                 status=excluded.status,payload_json=excluded.payload_json,
                 expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
            (
                owner_id,
                session_id,
                status,
                json.dumps(payload, ensure_ascii=False),
                time.time() + _STATE_TTL_SECONDS,
                utcnow(),
            ),
        )


def _clear_state(app, owner_id: str, session_id: str) -> None:
    with app.storage.connection() as conn:
        conn.execute(
            "DELETE FROM lenta_conversation_state WHERE owner_id=? AND session_id=?",
            (owner_id, session_id),
        )


def _pending_intent(app, text: str, owner_id: str, session_id: str | None) -> LentaIntent | None:
    state = _load_state(app, owner_id, session_id)
    if not state:
        return None
    low = _normalize(text)
    status = state["status"]
    if status == "login_required" and low in _LOGIN_DONE:
        return LentaIntent("login_done", text)
    if status in {"confirm_order", "basket_ready"}:
        if any(marker in low for marker in _MANUAL):
            return LentaIntent("manual_finish", text, "self")
        if status == "confirm_order" and low in _YES:
            return LentaIntent("confirm", text)
        if low in _NO:
            return LentaIntent("deny", text)
        if status == "basket_ready" and any(marker in low for marker in _ORDER):
            return LentaIntent("checkout_from_basket", text, _payment(text))
        if any(marker in low for marker in _MODIFY):
            return LentaIntent("modify", text, _payment(text))
    return None


def _enabled(app) -> bool:
    try:
        return module_enabled(app.storage, "lenta")
    except Exception as exc:
        problem("lenta_module_setting_failed", str(exc))
        return True


def _session(app, owner_id: str) -> LentaBrowserSession:
    return LentaSessionStore(app.settings.brain_dir).for_owner(owner_id)


def _module(app) -> LentaModule:
    value = getattr(app, "lenta_module", None)
    if not isinstance(value, LentaModule):
        value = LentaModule(app.settings.brain_dir)
        app.lenta_module = value
    return value


def _platform(app) -> BrowserPlatform:
    value = getattr(app, "browser_platform", None)
    if not isinstance(value, BrowserPlatform):
        value = BrowserPlatform(app)
        app.browser_platform = value
    return value


def _browser_task(
    app,
    owner_id: str,
    *,
    task: str,
    risk: BrowserRisk,
    approval_summary: str = "",
    max_steps: int = 40,
) -> str:
    profile = _session(app, owner_id)
    action_name = {
        BrowserRisk.READ: "agent_read",
        BrowserRisk.MUTATE: "agent_mutate",
        BrowserRisk.COMMIT: "agent_commit",
        BrowserRisk.FINANCIAL: "agent_financial",
    }[risk]
    arguments: dict[str, Any] = {
        "task": task,
        "allowed_hosts": ("lenta.com",),
        "profile_namespace": "lenta",
        "profile_key": profile.owner_key,
        "use_vision": False,
        "max_steps": max(8, min(int(max_steps), 70)),
    }
    if approval_summary:
        arguments["approval_summary"] = approval_summary
    result = _platform(app).agent.execute(
        BrowserAction(action_name, _LENTA_URL, risk, arguments, description=task),
        approved=risk is not BrowserRisk.READ,
    )
    if result.status != "ok":
        if result.status == "needs_user_action":
            raise RuntimeError("Сайт требует действия пользователя (CAPTCHA/challenge).")
        raise RuntimeError(result.message or "Браузерный модуль не выполнил действие")
    text = str(result.data.get("result") or result.message or "").strip()
    if not text:
        raise RuntimeError("Браузер не вернул подтверждённый результат")
    return text


def _login_required(text: str) -> bool:
    low = _normalize(text)
    return any(marker in low for marker in (
        "нужно войти", "требуется войти", "не авторизован", "не авторизована", "требуется авторизация",
        "войдите в аккаунт", "войти в аккаунт", "введите номер телефона", "нужно авторизоваться",
    ))


def _extract_total(text: str) -> str:
    value = str(text or "")
    patterns = (
        r"(?i)итого\s*[:\-]?\s*(\d[\d\s]*(?:[.,]\d{1,2})?\s*(?:₽|руб(?:\.|лей|ля|ль)?))",
        r"(?i)к\s+оплате\s*[:\-]?\s*(\d[\d\s]*(?:[.,]\d{1,2})?\s*(?:₽|руб(?:\.|лей|ля|ль)?))",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return " ".join(match.group(1).split())
    return ""


def _payment_label(value: str) -> str:
    return {
        "cash": "наличными при получении",
        "card_on_delivery": "картой при получении",
        "card_online": "картой онлайн",
        "self": "завершить самостоятельно",
    }.get(value, "вариант, выбранный в личном кабинете")


def _open_login(app, owner_id: str, session_id: str, resume: LentaIntent) -> tuple[str, list[dict[str, str]]]:
    profile = _session(app, owner_id)
    profile.open_login_window(_LENTA_URL)
    _save_state(
        app,
        owner_id,
        session_id,
        "login_required",
        {"resume": {"kind": resume.kind, "text": resume.text, "payment": resume.payment}},
    )
    return (
        "Открыл «Ленту» в Вашем персональном браузерном профиле. Войдите в личный кабинет сами "
        "(телефон, SMS/2FA вводите только на сайте), затем закройте открывшееся окно браузера "
        "и вернитесь сюда с сообщением «готово». Закрытие окна освобождает персональный профиль "
        "для Browser Agent; cookies и авторизация сохраняются. "
        "Сессию этого профиля Леший сохранит отдельно от других пользователей.",
        [{"title": "Лента", "url": _LENTA_URL}],
    )


def _capability_answer() -> str:
    return (
        "Да. В «Ленте» я могу проверить товары, цены и наличие, собрать или изменить корзину в Вашем личном "
        "кабинете и подготовить заказ. Корзину меняю без отдельного подтверждения каждого шага. Перед финальным "
        "оформлением заказа или оплатой обязательно покажу итог и спрошу подтверждение. Либо оставлю готовую "
        "корзину, чтобы Вы завершили заказ сами."
    )


def _catalog(app, owner_id: str, intent: LentaIntent) -> tuple[str, list[dict[str, str]]]:
    profile = _session(app, owner_id)
    state = profile.status()
    store_id = state.get("store_id")
    query = re.sub(r"https?://(?:www\.)?lenta\.com/?", " ", intent.text, flags=re.IGNORECASE)
    query = re.sub(r"(?i)\b(в|на|из)?\s*лент(?:а|е|у|ы|ой)\b", " ", query)
    query = re.sub(r"(?i)\b(сколько стоит|покажи цену|какая цена|найди товар|поищи товар|цена|стоимость)\b", " ", query)
    query = " ".join(query.strip(" .,:;!?-").split())

    if store_id and query:
        try:
            rows = _module(app).search(query, store_id=store_id, channel=str(state.get("channel") or "lo"), limit=6)
            priced = [row for row in rows if row.price is not None]
            if priced:
                lines = [f"Нашёл в выбранном магазине «Ленты» по запросу «{query}»: "]
                for row in priced[:5]:
                    availability = " · есть в наличии" if row.available is True else (" · нет в наличии" if row.available is False else "")
                    lines.append(f"• {row.name} — {row.price:g} ₽/{row.unit}{availability}")
                return "\n".join(lines), [{"title": "Лента", "url": _LENTA_URL}]
        except Exception as exc:
            problem("lenta_catalog_api_failed", str(exc), owner_id=owner_id)

    task = (
        "На сайте lenta.com найди товар и сообщи только подтверждённые текущие цены и наличие для магазина/региона, "
        "который уже выбран в этом браузерном профиле. Ничего не добавляй в корзину, не меняй аккаунт и не оформляй "
        f"заказ. Запрос пользователя: {intent.text}"
    )
    result = _browser_task(app, owner_id, task=task, risk=BrowserRisk.MUTATE, max_steps=28)
    if _login_required(result):
        return (
            "Не удалось получить персональные цены: сайт просит выбрать магазин или войти в аккаунт. "
            "Скажите «войти в Ленту», и я открою Ваш отдельный профиль браузера.",
            [{"title": "Лента", "url": _LENTA_URL}],
        )
    return result, [{"title": "Лента", "url": _LENTA_URL}]


def _basket_task(request_text: str, payment: str = "") -> str:
    payment_rule = ""
    if payment:
        payment_rule = f" Если сайт поддерживает этот вариант, выбери способ оплаты: {_payment_label(payment)}."
    return (
        "Работай только с корзиной текущего пользователя на lenta.com. Найди товары по запросу, добавь подходящие "
        "позиции в корзину, выставь количество и используй только реально доступные товары/цены. Можно заменять "
        "недоступный товар только очевидным близким аналогом и обязательно укажи замену в результате. "
        "НЕ нажимай финальную кнопку оформления/заказа и НЕ инициируй оплату."
        + payment_rule
        + " В конце верни краткое подтверждение: что лежит в корзине, количество, текущую итоговую сумму, "
          "доставку/самовывоз и выбранный способ оплаты, если они уже определены. Запрос пользователя: "
        + request_text
    )


def _prepare_basket(app, owner_id: str, session_id: str, intent: LentaIntent, *, ask_order: bool) -> tuple[str, list[dict[str, str]]]:
    profile = _session(app, owner_id)
    if profile.status().get("login_required", True):
        return _open_login(app, owner_id, session_id, intent)
    result = _browser_task(app, owner_id, task=_basket_task(intent.text, intent.payment), risk=BrowserRisk.MUTATE, max_steps=48)
    if _login_required(result):
        profile.mark_login_required("Сайт сообщил, что нужна авторизация")
        return _open_login(app, owner_id, session_id, intent)
    profile.mark_authenticated()
    payload = {
        "request": intent.text,
        "summary": result,
        "payment": intent.payment,
        "total": _extract_total(result),
    }
    if ask_order:
        _save_state(app, owner_id, session_id, "confirm_order", payload)
        answer = (
            "Корзина подготовлена. Я ещё НЕ оформлял заказ.\n\n"
            + result
            + "\n\nОформить заказ? Ответьте «да», «нет» или напишите, что изменить. "
              "Если хотите завершить заказ самостоятельно, напишите «сам оплачу»."
        )
    else:
        _save_state(app, owner_id, session_id, "basket_ready", payload)
        answer = (
            "Корзина подготовлена. Заказ не оформлял.\n\n"
            + result
            + "\n\nМожете завершить заказ сами в личном кабинете или написать мне «оформи заказ»."
        )
    return answer, [{"title": "Лента", "url": _LENTA_URL}]


def _prepare_checkout_from_basket(app, owner_id: str, session_id: str, intent: LentaIntent) -> tuple[str, list[dict[str, str]]]:
    state = _load_state(app, owner_id, session_id)
    previous = dict((state or {}).get("payload") or {})
    payment = intent.payment or str(previous.get("payment") or "")
    task = (
        "Открой текущую корзину пользователя на lenta.com и подготовь её к оформлению. Проверь наличие, итоговую "
        "сумму, способ получения, адрес/магазин и доступные способы оплаты. "
        + (f"Выбери {_payment_label(payment)}, только если сайт реально предлагает этот вариант. " if payment else "")
        + "НЕ нажимай финальную кнопку «Оформить заказ», «Заказать», «Оплатить» или эквивалент. "
          "В конце верни краткое резюме и ОБЯЗАТЕЛЬНО отдельную строку вида «ИТОГО: 1234 ₽»."
    )
    result = _browser_task(app, owner_id, task=task, risk=BrowserRisk.MUTATE, max_steps=40)
    if _login_required(result):
        return _open_login(app, owner_id, session_id, LentaIntent("checkout_from_basket", intent.text, payment))
    payload = {
        "request": str(previous.get("request") or intent.text),
        "summary": result,
        "payment": payment,
        "total": _extract_total(result),
    }
    _save_state(app, owner_id, session_id, "confirm_order", payload)
    return (
        "Заказ подготовлен, но финальную кнопку я не нажимал.\n\n"
        + result
        + "\n\nОформить заказ? Ответьте «да», «нет» или напишите, что изменить.",
        [{"title": "Лента", "url": _LENTA_URL}],
    )


def _modify_pending(app, owner_id: str, session_id: str, intent: LentaIntent) -> tuple[str, list[dict[str, str]]]:
    state = _load_state(app, owner_id, session_id)
    if not state:
        return "Не вижу подготовленной корзины, которую можно изменить.", []
    previous = dict(state.get("payload") or {})
    payment = intent.payment or str(previous.get("payment") or "")
    task = (
        "Измени текущую корзину/условия заказа на lenta.com строго по новой команде пользователя. Это обратимое "
        "изменение: НЕ оформляй заказ и НЕ инициируй оплату. После изменения снова проверь итог, способ получения и "
        "оплаты. Верни краткое резюме и строку «ИТОГО: N ₽». Новая команда: " + intent.text
    )
    result = _browser_task(app, owner_id, task=task, risk=BrowserRisk.MUTATE, max_steps=40)
    payload = {
        "request": str(previous.get("request") or ""),
        "summary": result,
        "payment": payment,
        "total": _extract_total(result),
    }
    target_status = "confirm_order" if state.get("status") == "confirm_order" else "basket_ready"
    _save_state(app, owner_id, session_id, target_status, payload)
    tail = (
        "\n\nОформить заказ? Ответьте «да», «нет» или напишите следующее изменение."
        if target_status == "confirm_order"
        else "\n\nКорзина обновлена. Заказ не оформлял."
    )
    return result + tail, [{"title": "Лента", "url": _LENTA_URL}]


def _confirm_order(app, owner_id: str, session_id: str) -> tuple[str, list[dict[str, str]]]:
    state = _load_state(app, owner_id, session_id)
    if not state or state.get("status") != "confirm_order":
        return "Нет заказа, ожидающего подтверждения.", []
    payload = dict(state.get("payload") or {})
    summary = str(payload.get("summary") or "").strip()
    payment = str(payload.get("payment") or "")
    total = str(payload.get("total") or "").strip()
    if payment == "self":
        _clear_state(app, owner_id, session_id)
        return "Корзина оставлена в личном кабинете. Дальше можете оформить и оплатить её самостоятельно.", [{"title": "Лента", "url": _LENTA_URL}]

    if payment == "card_online":
        if not total:
            return (
                "Не могу безопасно начать онлайн-оплату: не удалось однозначно подтвердить итоговую сумму. "
                "Корзина сохранена. Можете оплатить её самостоятельно или попросить меня ещё раз проверить итог.",
                [{"title": "Лента", "url": _LENTA_URL}],
            )
        risk = BrowserRisk.FINANCIAL
        approval_summary = f"Онлайн-оплата заказа «Ленты» на сумму {total}."
        task = (
            "Пользователь явно подтвердил показанный заказ. Ещё раз сверь итоговую сумму с подтверждённой: "
            f"{total}. Если сумма, состав корзины, адрес/способ получения или получатель изменились — остановись. "
            "Если всё совпадает, нажми финальное оформление и начни оплату сохранённым на сайте способом. "
            "Не вводи и не извлекай CVV, SMS/3DS-коды; если сайт их запросит, остановись и сообщи пользователю. "
            "Подтверждённое резюме: " + summary
        )
    else:
        risk = BrowserRisk.COMMIT
        approval_summary = ""
        task = (
            "Пользователь явно подтвердил показанный заказ. Ещё раз сверь состав корзины, итог, адрес/способ "
            "получения и способ оплаты с подготовленным резюме. Если что-либо существенно изменилось — остановись "
            "и не оформляй. Если всё совпадает, нажми финальную кнопку оформления заказа. Не переходи к отдельной "
            "онлайн-оплате без нового подтверждения. Подтверждённое резюме: " + summary
        )

    result = _browser_task(
        app,
        owner_id,
        task=task,
        risk=risk,
        approval_summary=approval_summary,
        max_steps=45,
    )
    _clear_state(app, owner_id, session_id)
    return result, [{"title": "Лента", "url": _LENTA_URL}]


def _execute(app, intent: LentaIntent, owner_id: str, session_id: str) -> tuple[str, list[dict[str, str]]]:
    if intent.kind == "capability":
        return _capability_answer(), [{"title": "Лента", "url": _LENTA_URL}]
    if intent.kind == "login":
        return _open_login(app, owner_id, session_id, LentaIntent("capability", intent.text))
    if intent.kind == "login_done":
        state = _load_state(app, owner_id, session_id)
        resume = dict(((state or {}).get("payload") or {}).get("resume") or {})
        _session(app, owner_id).mark_authenticated()
        _clear_state(app, owner_id, session_id)
        if not resume:
            return "Отметил вход в Ваш персональный профиль «Ленты». Теперь можно искать товары и собирать корзину.", [{"title": "Лента", "url": _LENTA_URL}]
        resumed = LentaIntent(str(resume.get("kind") or "catalog"), str(resume.get("text") or ""), str(resume.get("payment") or ""))
        return _execute(app, resumed, owner_id, session_id)
    if intent.kind == "catalog":
        return _catalog(app, owner_id, intent)
    if intent.kind == "basket":
        return _prepare_basket(app, owner_id, session_id, intent, ask_order=False)
    if intent.kind == "checkout":
        return _prepare_basket(app, owner_id, session_id, intent, ask_order=True)
    if intent.kind == "checkout_from_basket":
        return _prepare_checkout_from_basket(app, owner_id, session_id, intent)
    if intent.kind == "modify":
        return _modify_pending(app, owner_id, session_id, intent)
    if intent.kind == "confirm":
        return _confirm_order(app, owner_id, session_id)
    if intent.kind == "deny":
        _clear_state(app, owner_id, session_id)
        return "Хорошо, заказ не оформляю. Товары остаются в корзине.", [{"title": "Лента", "url": _LENTA_URL}]
    if intent.kind == "manual_finish":
        _clear_state(app, owner_id, session_id)
        return "Корзина остаётся в Вашем личном кабинете. Дальше можете оформить и оплатить заказ самостоятельно.", [{"title": "Лента", "url": _LENTA_URL}]
    raise ValueError("Неизвестное действие модуля «Лента»")


def _save_direct_user(app, message: str, owner_id: str, session_id: str | None) -> str:
    sid = app._ensure_session(owner_id, session_id)
    now = utcnow()
    with app.storage.connection() as conn:
        conn.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)", (sid, "user", message, now))
        count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
        if count == 1:
            conn.execute(
                "UPDATE sessions SET title=?,title_source=?,updated_at=? WHERE id=?",
                (" ".join(message.split())[:80], "auto", now, sid),
            )
    return sid


def _resolve(app, text: str, owner_id: str, session_id: str | None) -> LentaIntent | None:
    pending = _pending_intent(app, text, owner_id, session_id)
    return pending or _parse_explicit_intent(text)


def _install_browser_intent_exclusion() -> None:
    """Не давать общему Browser Agent перехватывать специализированные запросы «Ленты»."""
    global _ORIGINAL_BROWSER_INTENT_PARSER
    if _ORIGINAL_BROWSER_INTENT_PARSER is not None:
        return
    _ORIGINAL_BROWSER_INTENT_PARSER = browser_runtime.parse_browser_conversation_intent

    def parser(text: str):
        if _explicit_lenta(text):
            return None
        return _ORIGINAL_BROWSER_INTENT_PARSER(text)

    browser_runtime.parse_browser_conversation_intent = parser


def install_lenta_routing(app_class, app=None) -> None:
    """Подключить «Ленту» как отдельный fast-path до общей LLM."""
    global _INSTALLED, _ORIGINAL_CHAT, _ORIGINAL_STREAM
    _install_browser_intent_exclusion()
    if app is not None:
        _ensure_schema(app)
        _module(app)
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_STREAM = RealtimeChatManager.stream

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None, **_kwargs) -> dict[str, Any]:
        text = str(message or "").strip()
        owner = str(owner_id or "web:local").strip()[:200]
        intent = _resolve(self, text, owner, session_id) if _enabled(self) else None
        if intent is None:
            return _ORIGINAL_CHAT(self, text, owner_id=owner, session_id=session_id)
        sid = _save_direct_user(self, text, owner, session_id)
        try:
            answer, sources = _execute(self, intent, owner, sid)
        except Exception as exc:
            problem("lenta_chat_failed", str(exc), owner_id=owner, session_id=sid, action=intent.kind)
            answer = "Не удалось выполнить действие в «Ленте»: " + str(exc)
            sources = [{"title": "Лента", "url": _LENTA_URL}]
        created_at = self._save_assistant(sid, answer)
        event("lenta_chat_answered", owner_id=owner, session_id=sid, action=intent.kind)
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": created_at,
            "sources": sources,
            "lenta": True,
        }

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        text = str(message or "").strip()
        owner = str(owner_id or "web:local").strip()[:200]
        intent = _resolve(self.app, text, owner, session_id) if _enabled(self.app) else None
        if intent is None:
            yield from _ORIGINAL_STREAM(self, text, owner_id=owner, session_id=session_id, request_id=request_id)
            return

        rid, cancel = self._register(request_id)
        sid = ""
        try:
            sid, _rows = self._save_user_and_history(text, owner, session_id)
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "lenta": True, "browser_skill": True})
            if intent.kind not in {"capability", "deny", "manual_finish"}:
                yield StreamEvent("activity", {"kind": "lenta", "phase": "working", "text": "Работаю с «Лентой»…"})
            try:
                answer, sources = _execute(self.app, intent, owner, sid)
            except Exception as exc:
                problem("lenta_chat_stream_failed", str(exc), owner_id=owner, session_id=sid, action=intent.kind)
                answer = "Не удалось выполнить действие в «Ленте»: " + str(exc)
                sources = [{"title": "Лента", "url": _LENTA_URL}]
            if cancel.is_set():
                yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": "", "sources": [], "stopped": True, "lenta": True})
                return
            for chunk in self._chunk_fallback(answer, 180):
                if cancel.is_set():
                    break
                yield StreamEvent("delta", {"text": chunk})
            if cancel.is_set():
                yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": "", "sources": [], "stopped": True, "lenta": True})
                return
            answered_at = self.app._save_assistant(sid, answer)
            event("lenta_chat_answered", owner_id=owner, session_id=sid, action=intent.kind, request_id=rid)
            yield StreamEvent(
                "done",
                {
                    "request_id": rid,
                    "session_id": sid,
                    "answer": answer,
                    "created_at": answered_at,
                    "sources": sources,
                    "stopped": False,
                    "lenta": True,
                    "browser_skill": True,
                },
            )
        finally:
            self._unregister(rid)

    app_class.chat = chat
    RealtimeChatManager.stream = stream
    event("lenta_chat_routing_installed")


__all__ = [
    "LentaIntent",
    "install_lenta_routing",
    "_explicit_lenta",
    "_parse_explicit_intent",
    "_payment",
]
