from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Protocol
from urllib.parse import urlparse

from .diagnostics import event


class BrowserRisk(str, Enum):
    READ = "read"
    MUTATE = "mutate"
    COMMIT = "commit"
    FINANCIAL = "financial"


@dataclass(frozen=True, slots=True)
class BrowserAction:
    name: str
    url: str
    risk: BrowserRisk = BrowserRisk.READ
    arguments: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    status: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class BrowserAdapter(Protocol):
    adapter_id: str

    def available(self) -> bool: ...

    def execute(self, action: BrowserAction) -> BrowserActionResult: ...


class ApprovalRequired(RuntimeError):
    def __init__(self, action: BrowserAction):
        self.action = action
        super().__init__(f"Требуется подтверждение пользователя: {action.description or action.name}")


class BrowserAgent:
    """Единая точка browser-действий Лешего.

    READ выполняется автоматически. Изменяющие, подтверждающие и финансовые
    действия обязаны пройти Human Approval Gate. После проверки подтверждения
    адаптер получает внутренний `_approval_verified`, чтобы прямой вызов адаптера
    не мог случайно выглядеть как одобренное действие.
    """

    def __init__(self, adapters: list[BrowserAdapter], *, allowed_hosts: tuple[str, ...] = ()):
        self.adapters = list(adapters)
        self.allowed_hosts = tuple(x.lower().strip(".") for x in allowed_hosts if x)

    def _host_allowed(self, url: str) -> bool:
        # Проверка схемы/порта обязательна даже без явного allowlist:
        # file:, javascript:, ftp: и нестандартные порты не должны попадать в адаптер.
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            if parsed.username is not None or parsed.password is not None:
                return False
            port = parsed.port
            if port is not None and port != (443 if parsed.scheme == "https" else 80):
                return False
            host = parsed.hostname.lower().strip(".")
        except (TypeError, ValueError):
            return False
        if not self.allowed_hosts:
            return True
        return any(host == allowed or host.endswith("." + allowed) for allowed in self.allowed_hosts)

    def execute(self, action: BrowserAction, *, approved: bool = False) -> BrowserActionResult:
        if not self._host_allowed(action.url):
            event("browser_agent_blocked_host", url=action.url, action=action.name)
            return BrowserActionResult("blocked", "Домен не разрешён политикой Browser Agent.")

        if action.risk is not BrowserRisk.READ:
            if not approved:
                event("browser_agent_approval_required", action=action.name, risk=action.risk.value, url=action.url)
                raise ApprovalRequired(action)
            arguments = dict(action.arguments)
            arguments["_approval_verified"] = True
            action = replace(action, arguments=arguments)
            event("browser_agent_approval_verified", action=action.name, risk=action.risk.value, url=action.url)

        for adapter in self.adapters:
            try:
                if not adapter.available():
                    continue
                event("browser_agent_adapter_selected", adapter=adapter.adapter_id, action=action.name, risk=action.risk.value)
                result = adapter.execute(action)
            except Exception as exc:
                event("browser_agent_adapter_failed", adapter=getattr(adapter, "adapter_id", "unknown"), action=action.name, error=str(exc))
                continue
            if result.status not in {"unavailable", "unsupported"}:
                return result

        return BrowserActionResult("unavailable", "Нет доступного browser-адаптера для этого действия.")
