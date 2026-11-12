"""Determine whether the user is working or talking, with customer drafts separate.

No account-wide inference, time-of-day assumptions, employer identity or secret
ticket contents. Only the current message and this owner's current chat history
are used. The result is an advisory intent, never permission for external actions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class ConversationMode:
    kind: str  # personal | work | support_draft
    reason: str


_SUPPORT = (
    "/support", "/поддержка", "ответь клиенту", "ответ клиенту:",
    "напиши ответ клиенту", "составь ответ клиенту", "подготовь ответ клиенту",
    "сообщение клиента:", "обращение клиента:", "вопрос клиента:",
    "клиент пишет:", "клиент написал:", "клиент:", "ответ для клиента",
    "вот сообщение клиента:", "вот вопрос клиента:", "вот что пишет клиент:",
    "текст клиента:", "цитата клиента:", "пишет клиент:",
    "как сотрудник поддержки", "как сотрудник техподдержки",
)
_EDIT = (
    "сделай короче", "покороче", "убери приветствие", "без приветствия",
    "переформулируй", "переделай ответ", "исправь ответ",
    "напиши по-человечески", "сделай ответ", "ответь полностью",
    "добавь в ответ", "сделай официальнее", "мягче", "строже",
)
_PERSONAL = (
    "/chat", "/общение", "не по работе", "давай просто поговорим",
    "просто поболтаем", "поболтаем", "сменим тему", "давай отвлечемся",
    "расскажи шутку", "расскажи анекдот", "как твои дела",
    "как дела", "что нового у тебя", "хочу поговорить",
    "поговорим о жизни",
)
_WORK_EXPLICIT = (
    "/work", "/работа", "по работе", "рабочий вопрос", "работаем над",
    "вернемся к работе", "вернёмся к работе", "по заявке",
    "помоги разобраться с тикетом", "разберем тикет", "разберём тикет",
)
_WORK_TECH = (
    "kafka", "postgres", "elasticsearch", "linux", "windows server",
    "systemd", "journalctl", "nginx", "haproxy", "docker", "kubernetes",
    "ansible", "git push", "git pull", "github", "pull request",
    "репозитор", "коммит", "сборк", "исходник", "трассировк",
    "ошибка 500", "traceback", "stack trace", "скрипт", "программн",
    "тариф", "vps", "vds", "виртуальн", "сервер", "dns",
    "ssh", "rdp", "сеть", "маршрутиз", "тспу", "check-host",
    "лог фай", "логах", "логи", "техподдержк", "тикет",
    "клиентск", "заявк", "биллинг", "панель управлен",
)
_WORK_ACTION = (
    "исправь", "проверь", "настрой", "подключи", "добавь", "проведи",
    "разверни", "запусти", "исследуй", "сделай", "почини", "удали",
    "почему не", "не работает", "вылетает", "упал", "ошибка",
    "проблема", "не проходит", "продолжай", "собери",
)
_WORK_ISSUE = (
    "недоступ", "ошиб", "сбой", "не отвечает", "не поднимается",
    "обрыв", "снижен", "медлен", "потеря", "потери",
    "настройка", "сколько стоит", "какая цена", "почему",
    "статус", "как дела с",
)
_CONTINUATION = (
    "и что дальше", "а дальше", "что осталось", "а второй", "а теперь",
    "продолжай", "сделай это", "поправь", "исправь", "проверил",
    "получилось", "не помогло", "та же ошибка",
    "как дела с", "что по", "какой статус", "статус задачи",
)


def _norm(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _role_text(row: Any) -> tuple[str, str]:
    try:
        return str(row["role"]), str(row["content"])
    except (TypeError, KeyError, IndexError):
        return str(getattr(row, "role", "")), str(getattr(row, "content", ""))


def _starts_support(text: str) -> bool:
    return any(text.startswith(cue) for cue in _SUPPORT)


def _edit(text: str) -> bool:
    return any(cue in text for cue in _EDIT) and len(text) <= 180


def _is_work_content(text: str) -> bool:
    # A domain reference alone can be an ordinary educational question.
    if any(cue in text for cue in _WORK_EXPLICIT):
        return True
    has_domain = any(cue in text for cue in _WORK_TECH)
    has_action = any(cue in text for cue in _WORK_ACTION)
    has_issue = any(cue in text for cue in _WORK_ISSUE)
    return has_domain and (has_action or has_issue or len(text) > 95)


def resolve_conversation_mode(message: str, rows: Iterable[Any] = ()) -> ConversationMode:
    text = _norm(message)
    if not text:
        return ConversationMode("personal", "empty")
    # Explicit boundary always takes priority over old chat context.
    if any(text.startswith(cue) for cue in ("/chat", "/общение", "не по работе")):
        return ConversationMode("personal", "explicit_personal")
    if _starts_support(text):
        return ConversationMode("support_draft", "customer_message")
    # Strong signs of pasted correspondence directed to the provider.
    if text.startswith(("здравствуйте", "добрый день")) and (
        any(cue in text for cue in ("у вас на", "ваш хостинг", "вашей компании",
                                   "вашем хостинге", "у вас можно"))
        and any(cue in text for cue in ("vps", "сервер", "хостинг", "vds", "тариф"))
        and "?" in text
    ):
        return ConversationMode("support_draft", "quoted_customer_question")
    casual_explicit = (
        "не по работе", "давай просто поговорим", "просто поболтаем",
        "поболтаем", "сменим тему", "давай отвлечемся",
        "расскажи шутку", "расскажи анекдот", "поговорим о жизни",
    )
    if any(cue in text for cue in casual_explicit):
        return ConversationMode("personal", "personal_intent")
    if text.startswith(("/work", "/работа")):
        return ConversationMode("work", "explicit_work")
    if _is_work_content(text):
        return ConversationMode("work", "technical_work")
    if any(cue in text for cue in _PERSONAL):
        return ConversationMode("personal", "personal_intent")

    # History is bounded and same-session only; if recent user conversation
    # moved to unrelated topics, do not inherit support/work routing forever.
    previous: list[str] = []
    for row in list(rows)[:14]:
        role, content = _role_text(row)
        old = _norm(content)
        if role != "user" or not old or old == text:
            continue
        previous.append(old)
        if len(previous) >= 3:
            break
    if not previous:
        return ConversationMode("personal", "default")
    latest = previous[0]
    if any(marker in latest for marker in _PERSONAL) and not _is_work_content(latest):
        return ConversationMode("personal", "prior_personal")

    # A few consecutive style-edit requests still belong to the same ticket.
    # One unrelated message breaks the sequence; never revive an old ticket.
    if _edit(text):
        for old in previous:
            if _starts_support(old):
                return ConversationMode("support_draft", "ticket_revision")
            if not _edit(old):
                break

    # Short status/continuation messages can follow several technical turns.
    # A casual topic change stops inheriting that context.
    if any(cue in text for cue in _CONTINUATION):
        for old in previous:
            if _is_work_content(old):
                return ConversationMode("work", "technical_followup")
            if not any(cue in old for cue in _CONTINUATION):
                break
    return ConversationMode("personal", "default")


def mode_instruction(kind: str) -> str:
    if kind == "work":
        return (
            "ТЕКУЩИЙ КОНТЕКСТ: совместная работа с пользователем над технической задачей. "
            "Отвечай как технический коллега: коротко, конкретно, с действиями и проверяемыми "
            "фактами. Это общение с владельцем, а не готовое письмо клиенту. "
            "Не вводи в ответ сведения из закрытого справочника организации без "
            "относящегося к делу рабочего вопроса. Не заявляй, что работы выполнены, "
            "без подтверждения инструмента."
        )
    if kind == "personal":
        return (
            "ТЕКУЩИЙ КОНТЕКСТ: обычная беседа с владельцем. Общайся естественно, "
            "не переходи в роль сотрудника техподдержки, не оформляй реплики как "
            "тикеты и не добавляй корпоративные тарифы или внутренние регламенты. "
            "Если собеседник попросит составить ответ клиенту, отдельный маршрут "
            "сам переключит режим."
        )
    return ""


__all__ = ["ConversationMode", "resolve_conversation_mode", "mode_instruction"]
