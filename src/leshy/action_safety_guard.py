from __future__ import annotations

"""Независимая от LLM проверка явных опасных инструкций перед внешними действиями.

Это дополнительная граница, а не полная семантическая защита: разрешение
пользователя и проверка модели не отменяют прямых запретов.
"""

import json
import re
from typing import Any, Mapping

from .safety import SafetyDecision, SafetyPolicy


# Правила касаются команд с внешним эффектом, а не обсуждения тем в чате.
# Не включаем здесь общие слова «сеть», «безопасность», «суицид».
_PROHIBITED_ACTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("network_attack", re.compile(r"(?i)\b(?:ddos|slowloris|syn[\s_-]*flood|udp[\s_-]*flood|http[\s_-]*flood)\b")),
    ("physical_harm", re.compile(r"(?i)(?:отрав(?:и|ить|ить\s+кого)|poison\s+(?:a\s+|the\s+)?(?:person|human|animal))")),
    ("suicide_coercion", re.compile(r"(?i)(?:(?:довест[ьи]|склон(?:и|ить)).{0,80}(?:самоубийств|суицид)|(?:drive|coerce).{0,50}(?:suicide|self.harm))")),
    ("credential_theft", re.compile(r"(?i)(?:укра(?:ди|сть)|похит(?:и|ить)).{0,60}(?:парол|токен|ключ)|(?:steal|exfiltrate).{0,40}(?:password|token|credential)")),
)

# Содержимое создаваемых пользователем файлов не интерпретируем как команду.
# Сами shell/process-параметры проверяются; данные read_text не анализируются.
_EXEC_KEYS = frozenset({"command", "program", "args", "arguments", "script", "target", "url", "host"})
_READ_ACTIONS = frozenset({"system_status", "list_dir", "read_text", "process_list"})


def check_external_action(action: str, arguments: Mapping[str, Any] | None = None) -> SafetyDecision:
    """При явной опасной задаче отказать даже после approval.

    Шлюз проверяет *переданное действие*, а не обрабатывает произвольные
    тексты источников. Остальные права, подписи, квоты и подтверждения
    проверяются в соответствующих исполнителях отдельно.
    """
    name = str(action or "").strip()
    if not name:
        raise ValueError("Не задано имя внешнего действия")
    if arguments is not None and not isinstance(arguments, Mapping):
        raise ValueError("Аргументы внешнего действия должны быть объектом")
    data = dict(arguments or {})
    parts = [name]
    if name.lower() not in _READ_ACTIONS:
        for key, value in data.items():
            if key.lower() in _EXEC_KEYS:
                parts.append(json.dumps(value, ensure_ascii=False, default=str)[:4096])
    instruction = " ".join(parts)
    for category, expression in _PROHIBITED_ACTIONS:
        if expression.search(instruction):
            raise PermissionError(
                f"Операция отклонена независимой политикой безопасности: {category}"
            )
    decision = SafetyPolicy().evaluate_action(instruction)
    if not decision.allowed:
        raise PermissionError(
            f"Операция отклонена независимой политикой безопасности: {decision.category}"
        )
    return decision


__all__ = ["check_external_action"]
