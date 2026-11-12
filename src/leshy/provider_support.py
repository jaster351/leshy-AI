"""Generic support drafting. Sensitive provider context is local and encrypted."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class SupportFact:
    key: str
    cues: tuple[str, ...]
    statement: str


FACTS: tuple[SupportFact, ...] = (
    SupportFact("network", ("скорост", "мбит", "канал", "iperf"),
                "Тарифная, фактическая и гарантированная полоса могут различаться; сверяй условия заказа."),
    SupportFact("trace", ("mtr", "потер", "маршрут", "ping"),
                "ICMP-потери на промежуточных хопах не доказывают потери трафика до конечного узла."),
    SupportFact("tcp", ("порт", "tcp", "linux", "windows", "ssh", "rdp"),
                "Открытый порт — признак доступного сервиса, но не доказательство ОС. Тайм-аут не доказывает блокировку."),
    SupportFact("billing", ("возврат", "оплат", "тариф", "цен", "договор"),
                "Действующие цены, возвраты и скидки проверяются по текущему заказу и оферте."),
    SupportFact("changes", ("переустанов", "останов", "удален", "сервер", "тикет"),
                "Перед изменением услуги сверяй её идентификатор и получай согласие на остановку или потерю данных."),
)

STYLE = (
    "Составь РОВНО ОДИН готовый ответ клиенту от лица специалиста техподдержки. "
    "По-русски, на «Вы», естественно, кратко. Без приветствия в продолжающемся тикете, "
    "подписи, формулировки «вот ответ», метакомментариев и обвинений клиента. "
    "Сначала отвечай по существу. Не выдумывай цены, гарантии, выполненные действия "
    "или результаты диагностики. Не раскрывай чужие IP и клиентские секреты. "
    "Не отправляй сообщение автоматически. "
    "Не подменяй установленные факты предположениями."
)

_EXPLICIT = (
    "/support", "/поддержка", "ответь клиенту", "ответ клиенту",
    "напиши ответ клиенту", "составь ответ клиенту", "вопрос клиента",
    "сообщение клиента", "обращение клиента", "клиент пишет",
    "как сотрудник поддержки", "как сотрудник техподдержки",
    "подготовь ответ", "черновик ответа",
)
_REPLY_EDITS = (
    "убери приветствие", "без приветствия", "короче", "покороче",
    "переформулируй", "переделай ответ", "напиши по-человечески",
    "сделай ответ", "ответь полностью",
)


def _normal(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _row_text(row: Any) -> tuple[str, str]:
    try:
        return str(row["role"]), str(row["content"])
    except (KeyError, IndexError, TypeError):
        return str(getattr(row, "role", "")), str(getattr(row, "content", ""))


def is_support_draft_request(message: str, rows: Iterable[Any] = ()) -> bool:
    text = _normal(message)
    if any(cue in text for cue in _EXPLICIT):
        return True
    if not any(cue in text for cue in _REPLY_EDITS):
        return False
    recent = []
    for row in list(rows)[:12]:
        role, old = _row_text(row)
        if role != "user" or _normal(old) == text:
            continue
        recent.append(_normal(old))
        if len(recent) >= 4:
            break
    return any(any(cue in previous for cue in _EXPLICIT) for previous in recent)


def relevant_support_facts(query: str, *, limit: int = 6) -> tuple[SupportFact, ...]:
    text = _normal(query)
    ranked = []
    for fact in FACTS:
        matches = [cue for cue in fact.cues if _normal(cue) in text]
        if matches:
            ranked.append((max(map(len, matches)) + len(matches), fact))
    ranked.sort(key=lambda record: record[0], reverse=True)
    return tuple(fact for _, fact in ranked[:max(1, min(int(limit), 12))])


def _private_facts(query: str, brain_dir: Path | str | None) -> tuple[str, ...]:
    if not brain_dir:
        return ()
    path = Path(brain_dir) / "private" / "support_profile.enc"
    from .private_support_bootstrap import decrypt_support_profile, support_key
    key = support_key(brain_dir)
    if key is None or not path.is_file() or path.is_symlink():
        return ()
    try:
        if path.stat().st_size > 196608:
            return ()
        value = decrypt_support_profile(path.read_bytes(), key)
        if value is None:
            return ()
    except OSError:
        # Failed decryption is not evidence of policy; never expose ciphertext/key.
        return ()
    matches = []
    text = _normal(query)
    for row in value["facts"][:128]:
        if not isinstance(row, dict):
            continue
        cues, statement = row.get("cues"), row.get("text")
        if not isinstance(cues, list) or not isinstance(statement, str):
            continue
        if any(isinstance(cue, str) and _normal(cue) in text for cue in cues[:16]):
            matches.append(statement[:1400])
        if len(matches) >= 6:
            break
    return tuple(matches)


def support_draft_context(
    message: str,
    rows: Iterable[Any] = (),
    *,
    force: bool = False,
    brain_dir: Path | str | None = None,
) -> str:
    if not force and not is_support_draft_request(message, rows):
        return ""
    facts = ["- " + item.statement for item in relevant_support_facts(message)]
    facts.extend("- Частный локальный справочник: " + item for item in _private_facts(message, brain_dir))
    if not facts:
        facts.append("- Если правила поставщика не установлены, не придумывай их.")
    return "РЕЖИМ ЧЕРНОВИКА ТЕХПОДДЕРЖКИ:\n" + STYLE + "\n\n" + "\n".join(facts)
