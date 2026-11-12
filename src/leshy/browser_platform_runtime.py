from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from threading import RLock
from typing import Any, Iterator
from urllib.parse import urlparse

from .action_approvals import ApprovalBroker
from .browser_agent import BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk
from .browser_use_adapter import BrowserUseAdapter
from .browser_use_llm import LeshyBrowserUseLLM
from .diagnostics import event, problem
from .document_intelligence import DocumentIntelligence
from .extension_marketplace import ExtensionMarketplace
from .module_settings import module_enabled
from .playwright_browser_adapter import PlaywrightBrowserAdapter
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow
from .tools_gateway import McpToolsGateway, ToolRegistry


_URL_RE = re.compile(r"https?://[^\s<>\]\[()]+", re.IGNORECASE)
_AMOUNT_RE = re.compile(
    r"(?P<amount>\d[\d\s]*(?:[.,]\d{1,2})?)\s*(?P<currency>₽|руб(?:лей|ля|ль)?|р\.?|usd|eur|доллар(?:ов|а)?|евро)",
    re.IGNORECASE,
)
_ORDINALS = {
    "перв": 0, "1 вариант": 0, "вариант 1": 0,
    "втор": 1, "2 вариант": 1, "вариант 2": 1,
    "трет": 2, "3 вариант": 2, "вариант 3": 2,
}
_RESEARCH_VERBS = ("найд", "поищ", "подбер", "сравн", "посмотр", "проверь", "выбери")
_COMMERCE_NOUNS = (
    "товар", "магазин", "цена", "билет", "авиабилет", "самолет", "самолёт", "поезд",
    "отель", "гостиниц", "номер", "рейс", "заказ", "брон", "достав", "маркетплейс",
)
_COMMIT_VERBS = ("купи", "купить", "закаж", "оформи", "заброниру", "подтверди брон", "заброн")
_FINANCIAL_VERBS = ("оплати", "оплатить", "заплати", "проведи оплат", "подтверди оплат")

_RESEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_research_results(
    session_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    snippet TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(session_id, position)
);
CREATE INDEX IF NOT EXISTS ix_browser_research_session
ON browser_research_results(session_id, position);
"""


@dataclass(frozen=True, slots=True)
class BrowserConversationIntent:
    kind: str
    text: str
    url: str = ""
    selected_index: int | None = None
    amount_summary: str = ""


def parse_browser_conversation_intent(text: str) -> BrowserConversationIntent | None:
    value = " ".join(str(text or "").split()).strip()
    low = value.lower().replace("ё", "е")
    if not value:
        return None
    if low.startswith("/browser "):
        return BrowserConversationIntent("research", value[len("/browser "):].strip())

    urls = _URL_RE.findall(value)
    amount = _AMOUNT_RE.search(value)
    amount_summary = amount.group(0) if amount else ""
    selected: int | None = None
    for marker, index in _ORDINALS.items():
        if marker in low:
            selected = index
            break

    if any(marker in low for marker in _FINANCIAL_VERBS):
        return BrowserConversationIntent("financial", value, urls[0] if urls else "", selected, amount_summary)

    commit_requested = any(marker in low for marker in _COMMIT_VERBS)
    if commit_requested and (urls or selected is not None or "этот" in low or "его" in low):
        return BrowserConversationIntent("commit", value, urls[0] if urls else "", selected)

    if urls and any(marker in low for marker in ("открой", "изучи", "проверь", "посмотри", "сравни")):
        return BrowserConversationIntent("research_url", value, urls[0])

    if commit_requested:
        return BrowserConversationIntent("research", value)
    if any(verb in low for verb in _RESEARCH_VERBS) and any(noun in low for noun in _COMMERCE_NOUNS):
        return BrowserConversationIntent("research", value)
    return None


class BrowserPlatform:
    def __init__(self, app):
        self.app = app
        self.browser_use = BrowserUseAdapter(LeshyBrowserUseLLM(app.llm))
        self.playwright = PlaywrightBrowserAdapter()
        self.agent = BrowserAgent([self.browser_use, self.playwright])
        self.approvals = ApprovalBroker(ttl_seconds=600)
        self.tools_registry = ToolRegistry()
        self.tools_gateway = McpToolsGateway(self.tools_registry)
        self.marketplace = ExtensionMarketplace(self.tools_registry)
        self.documents = DocumentIntelligence()
        self._research: dict[str, list[dict[str, str]]] = {}
        self._lock = RLock()
        with self.app.storage.connection() as conn:
            conn.executescript(_RESEARCH_SCHEMA)

    def enabled(self) -> bool:
        try:
            return module_enabled(self.app.storage, "browser-agent")
        except Exception:
            return True

    @staticmethod
    def _profile_arguments(owner_id: str) -> dict[str, str]:
        """Изолировать browser cookies по проверенному owner, не раскрывая его в пути."""
        owner = str(owner_id or "").strip()
        if not owner:
            raise ValueError("Browser Agent требует идентификатор владельца")
        return {"profile_namespace": "owner", "profile_key": sha256(owner.encode("utf-8")).hexdigest()}

    @staticmethod
    def _host(url: str) -> str:
        return (urlparse(str(url or "")).hostname or "").lower().strip(".")

    @staticmethod
    def _sanitize_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        safe: list[dict[str, str]] = []
        for row in rows[:12]:
            url = str(row.get("url") or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            safe.append({
                "title": str(row.get("title") or url)[:500],
                "url": url[:4000],
                "snippet": str(row.get("snippet") or "")[:1500],
            })
        return safe

    def _remember_results(self, session_id: str, rows: list[dict[str, Any]]) -> None:
        sid = str(session_id)
        safe = self._sanitize_rows(rows)
        with self._lock:
            self._research[sid] = safe
        now = utcnow()
        with self.app.storage.connection() as conn:
            conn.execute("DELETE FROM browser_research_results WHERE session_id=?", (sid,))
            for position, row in enumerate(safe):
                conn.execute(
                    "INSERT INTO browser_research_results(session_id,position,title,url,snippet,updated_at) VALUES(?,?,?,?,?,?)",
                    (sid, position, row["title"], row["url"], row["snippet"], now),
                )
        event("browser_research_state_saved", session_id=sid, results=len(safe))

    def _last_results(self, session_id: str) -> list[dict[str, str]]:
        sid = str(session_id)
        with self._lock:
            cached = self._research.get(sid)
            if cached is not None:
                return list(cached)
        with self.app.storage.connection() as conn:
            rows = conn.execute(
                "SELECT title,url,snippet FROM browser_research_results WHERE session_id=? ORDER BY position",
                (sid,),
            ).fetchall()
        restored = [
            {"title": str(row["title"]), "url": str(row["url"]), "snippet": str(row["snippet"])}
            for row in rows
        ]
        with self._lock:
            self._research[sid] = restored
        if restored:
            event("browser_research_state_restored", session_id=sid, results=len(restored))
        return list(restored)

    @staticmethod
    def _fallback_summary(rows: list[dict[str, str]]) -> str:
        if not rows:
            return "Не нашёл подтверждённых вариантов в интернете."
        lines = ["Нашёл варианты:"]
        for index, item in enumerate(rows[:8], start=1):
            snippet = " ".join(str(item.get("snippet") or "").split())
            suffix = f" — {snippet[:260]}" if snippet else ""
            lines.append(f"{index}. {item.get('title') or item.get('url')}\n{item.get('url')}{suffix}")
        lines.append("\nВыберите вариант, например: «забронируй первый» или «купи вариант 2». Перед действием Леший отдельно попросит подтверждение.")
        return "\n".join(lines)

    def research(self, query: str, *, owner_id: str, session_id: str) -> tuple[str, list[dict[str, str]]]:
        result = self.app.live_search(query, limit=8, reason="Browser Agent: запрос пользователя")
        rows = list(result.get("results") or [])
        self._remember_results(session_id, rows)
        safe_rows = self._last_results(session_id)
        if not safe_rows:
            return self._fallback_summary([]), []

        hosts = tuple(dict.fromkeys(self._host(row["url"]) for row in safe_rows if self._host(row["url"])))
        action = BrowserAction(
            "research",
            safe_rows[0]["url"],
            BrowserRisk.READ,
            {
                "task": (
                    "Исследуй и сравни варианты по запросу пользователя. Используй только разрешённые сайты. "
                    "Верни короткое сравнение с ценой, условиями и датами, если они видны, и URL лучших вариантов. "
                    f"Запрос: {query}"
                ),
                "allowed_hosts": hosts,
                "use_vision": False,
                "max_steps": 28,
                **self._profile_arguments(owner_id),
            },
            description=query,
        )
        browser_result = self.agent.execute(action)
        if browser_result.status == "ok" and str(browser_result.data.get("result") or "").strip():
            answer = str(browser_result.data["result"]).strip()
            answer += "\n\nПеред оформлением выбранного варианта Леший запросит отдельное подтверждение."
        else:
            answer = self._fallback_summary(safe_rows)
        sources = [{"title": row["title"], "url": row["url"]} for row in safe_rows]
        return answer, sources

    def research_url(self, intent: BrowserConversationIntent, *, owner_id: str, session_id: str) -> tuple[str, list[dict[str, str]]]:
        host = self._host(intent.url)
        if not host:
            raise ValueError("Некорректный URL")
        action = BrowserAction(
            "research",
            intent.url,
            BrowserRisk.READ,
            {"task": intent.text, "allowed_hosts": (host,), "use_vision": False, "max_steps": 20, **self._profile_arguments(owner_id)},
            description=intent.text,
        )
        result = self.agent.execute(action)
        if result.status != "ok":
            raise RuntimeError(result.message)
        self._remember_results(session_id, [{"title": intent.url, "url": intent.url, "snippet": ""}])
        return str(result.data.get("result") or result.message), [{"title": intent.url, "url": intent.url}]

    def _selected_url(self, intent: BrowserConversationIntent, session_id: str) -> str:
        if intent.url:
            return intent.url
        rows = self._last_results(session_id)
        if not rows:
            raise ValueError("Сначала нужно найти варианты или прислать ссылку.")
        if intent.selected_index is not None:
            if intent.selected_index < 0 or intent.selected_index >= len(rows):
                raise ValueError("Такого номера варианта нет в последних результатах.")
            return rows[intent.selected_index]["url"]
        if len(rows) == 1:
            return rows[0]["url"]
        raise ValueError("Укажите номер варианта, например «забронируй первый».")

    def _require_owned_approval_session(self, owner_id: str, session_id: str) -> None:
        """Проверять существующую сессию в БД, не создавать её из approval-запроса."""
        owner = str(owner_id or "").strip()
        sid = str(session_id or "").strip()
        if not owner or owner == "web:local" or len(owner) > 200 or not sid or len(sid) > 200:
            raise PermissionError("Требуется действующая сессия пользователя")
        with self.app.storage.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE id=? AND owner_id=?", (sid, owner)
            ).fetchone()
        if row is None:
            raise PermissionError("Подтверждение не найдено")

    def request_external_action(self, intent: BrowserConversationIntent, *, owner_id: str, session_id: str) -> dict[str, Any]:
        self._require_owned_approval_session(owner_id, session_id)
        url = self._selected_url(intent, session_id)
        host = self._host(url)
        if not host:
            raise ValueError("Не удалось определить домен выбранного варианта")

        if intent.kind == "financial":
            if not intent.amount_summary:
                raise ValueError("Перед оплатой укажите подтверждаемую сумму и валюту, например «оплати 12 500 ₽».")
            risk = BrowserRisk.FINANCIAL
            action_name = "agent_financial"
            summary = f"Оплата {intent.amount_summary} на {host}. Задача: {intent.text}"
            arguments = {
                "task": intent.text,
                "allowed_hosts": (host,),
                "approval_summary": summary,
                "use_vision": False,
                "max_steps": 35,
                **self._profile_arguments(owner_id),
            }
        else:
            risk = BrowserRisk.COMMIT
            action_name = "agent_commit"
            summary = f"Внешнее действие на {host}: {intent.text}"
            arguments = {"task": intent.text, "allowed_hosts": (host,), "use_vision": False, "max_steps": 35, **self._profile_arguments(owner_id)}

        pending = self.approvals.request(
            owner_id,
            BrowserAction(action_name, url, risk, arguments, description=intent.text),
            summary=summary,
            session_id=session_id,
        )
        return pending.public()

    def resolve_approval(self, owner_id: str, approval_id: str, *, session_id: str, approved: bool) -> BrowserActionResult:
        self._require_owned_approval_session(owner_id, session_id)
        if not approved:
            self.approvals.deny(owner_id, approval_id, session_id=session_id)
            return BrowserActionResult("cancelled", "Действие отменено пользователем.")
        # Resolve against the broker's own immutable action. No caller-supplied
        # approved=True flag can bypass the gate; consume is atomic and one-time.
        action = self.approvals.peek_action(owner_id, approval_id, session_id=session_id)
        result = self.agent.execute_approved(action, broker=self.approvals,
                                             approval_id=approval_id, owner_id=owner_id,
                                             session_id=session_id)
        event(
            "browser_platform_approved_action_finished",
            approval_id=approval_id,
            status=result.status,
            action=action.name,
            risk=action.risk.value,
        )
        return result

    def handle(self, text: str, *, owner_id: str, session_id: str) -> tuple[str, list[dict[str, str]], dict[str, Any] | None]:
        intent = parse_browser_conversation_intent(text)
        if intent is None:
            raise ValueError("Сообщение не является Browser Agent intent")
        if intent.kind == "research":
            answer, sources = self.research(intent.text, owner_id=owner_id, session_id=session_id)
            return answer, sources, None
        if intent.kind == "research_url":
            answer, sources = self.research_url(intent, owner_id=owner_id, session_id=session_id)
            return answer, sources, None
        pending = self.request_external_action(intent, owner_id=owner_id, session_id=session_id)
        answer = (
            "Действие подготовлено, но ещё НЕ выполнено. Проверьте условия и подтвердите его в панели Лешего.\n\n"
            f"{pending['summary']}\nСрок подтверждения: до {pending['expires_at']}."
        )
        return answer, [{"title": "Выбранный сайт", "url": pending["url"]}], pending


_INSTALLED = False
_ORIGINAL_CHAT = None
_ORIGINAL_STREAM = None


def install_browser_platform_routing(app_class, app=None) -> None:
    global _INSTALLED, _ORIGINAL_CHAT, _ORIGINAL_STREAM
    if app is not None and not isinstance(getattr(app, "browser_platform", None), BrowserPlatform):
        app.browser_platform = BrowserPlatform(app)
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_STREAM = RealtimeChatManager.stream

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None, **_kwargs):
        intent = parse_browser_conversation_intent(message)
        platform = getattr(self, "browser_platform", None)
        if intent is None or not isinstance(platform, BrowserPlatform) or not platform.enabled():
            return _ORIGINAL_CHAT(self, message, owner_id=owner_id, session_id=session_id)

        sid = self._ensure_session(owner_id, session_id)
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (sid, "user", str(message), now),
            )
            count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
            if count == 1:
                conn.execute(
                    "UPDATE sessions SET title=?,updated_at=? WHERE id=?",
                    (" ".join(str(message).split())[:80], now, sid),
                )
        try:
            answer, sources, pending = platform.handle(str(message), owner_id=owner_id, session_id=sid)
        except Exception as exc:
            problem("browser_platform_chat_failed", str(exc), session_id=sid)
            answer, sources, pending = str(exc), [], None
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": self._save_assistant(sid, answer),
            "sources": sources,
            "browser_agent": True,
            "approval": pending,
        }

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        intent = parse_browser_conversation_intent(message)
        platform = getattr(self.app, "browser_platform", None)
        if intent is None or not isinstance(platform, BrowserPlatform) or not platform.enabled():
            yield from _ORIGINAL_STREAM(
                self,
                message,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

        rid, cancel = self._register(request_id)
        sid = ""
        try:
            sid, _rows = self._save_user_and_history(str(message), owner_id, session_id)
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "browser_agent": True})
            yield StreamEvent("activity", {"kind": "browser_agent", "phase": "working", "text": "Работаю в браузере…"})
            try:
                answer, sources, pending = platform.handle(str(message), owner_id=owner_id, session_id=sid)
            except Exception as exc:
                problem("browser_platform_stream_failed", str(exc), session_id=sid)
                answer, sources, pending = str(exc), [], None

            if cancel.is_set():
                yield StreamEvent(
                    "done",
                    {"request_id": rid, "session_id": sid, "answer": "", "sources": [], "stopped": True, "browser_agent": True},
                )
                return

            for chunk in self._chunk_fallback(answer, 180):
                if cancel.is_set():
                    break
                yield StreamEvent("delta", {"text": chunk})
            if cancel.is_set():
                yield StreamEvent(
                    "done",
                    {"request_id": rid, "session_id": sid, "answer": "", "sources": [], "stopped": True, "browser_agent": True},
                )
                return

            answered_at = self.app._save_assistant(sid, answer)
            yield StreamEvent(
                "done",
                {
                    "request_id": rid,
                    "session_id": sid,
                    "answer": answer,
                    "created_at": answered_at,
                    "sources": sources,
                    "stopped": False,
                    "browser_agent": True,
                    "approval": pending,
                },
            )
        finally:
            self._unregister(rid)

    app_class.chat = chat
    RealtimeChatManager.stream = stream


__all__ = [
    "BrowserConversationIntent",
    "BrowserPlatform",
    "install_browser_platform_routing",
    "parse_browser_conversation_intent",
]
