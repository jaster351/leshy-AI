from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import ipaddress
import re
from typing import Any, Protocol
from urllib.parse import urlparse

from .diagnostics import event


def _browser_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Распознать IP, включая числовые IPv4-формы, понимаемые браузерами."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # Браузеры принимают 127.1, 0177.0.0.1, 0x7f000001 и 2130706433.
    # Не интерпретировать обычные DNS-имена как адреса.
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        return None
    numbers = []
    for part in parts:
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", part):
            base = 16
        elif re.fullmatch(r"0[0-7]*", part):
            base = 8
        elif re.fullmatch(r"[1-9][0-9]*", part):
            base = 10
        else:
            return None
        numbers.append(int(part, base))
    if any(number >= (1 << (8 if index < len(numbers) - 1 else 8 * (5 - len(numbers))))
           for index, number in enumerate(numbers)):
        return None
    value = 0
    for number in numbers[:-1]:
        value = (value << 8) | number
    value = (value << (8 * (5 - len(numbers)))) | numbers[-1]
    try:
        return ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return None


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
            host = parsed.hostname.lower().strip(".")
            # .localhost зарезервирован для loopback: проверка только IP недостаточна.
            # Исключение разрешается исключительно для точного имени хоста.
            if (host == "localhost" or host.endswith(".localhost")) and host not in self.allowed_hosts:
                return False
            ip = _browser_ip_literal(host)
            if ip is not None and not ip.is_global and host not in self.allowed_hosts:
                return False
            # Только явно разрешённый loopback может использовать случайный
            # порт локального тестового HTTP-сервера; внешние порты запрещены.
            loopback_allowed = host in {"127.0.0.1", "::1", "localhost"} and host in self.allowed_hosts
            if port is not None and port != (443 if parsed.scheme == "https" else 80) and not loopback_allowed:
                return False
        except (TypeError, ValueError):
            return False
        if not self.allowed_hosts:
            return True
        return any(host == allowed or host.endswith("." + allowed) for allowed in self.allowed_hosts)

    def execute_approved(self, action: BrowserAction, *, broker: Any, approval_id: str,
                         owner_id: str, session_id: str) -> BrowserActionResult:
        """Execute only the exact action atomically consumed from the trusted broker.

        A caller-supplied action is never authoritative: a mismatch consumes no grant.
        No approval token or verified flag is accepted from tool/model arguments.
        """
        verified_action = broker.consume_matching(owner_id, approval_id, action, session_id=session_id)
        return self._execute(verified_action, verified=True)

    def execute(self, action: BrowserAction, *, approved: bool = False) -> BrowserActionResult:
        return self._execute(action, verified=False, legacy_approved=approved)

    def _execute(self, action: BrowserAction, *, verified: bool,
                 legacy_approved: bool = False) -> BrowserActionResult:
        # Маркер подтверждения является внутренним: не доверять значению,
        # поступившему через arguments от модели, инструмента или API.
        # READ также не должен передавать поддельное подтверждение адаптерам.
        arguments = dict(action.arguments)
        arguments.pop("_approval_verified", None)
        action = replace(action, arguments=arguments)
        if not self._host_allowed(action.url):
            event("browser_agent_blocked_host", url=action.url, action=action.name)
            return BrowserActionResult("blocked", "Домен не разрешён политикой Browser Agent.")

        if action.risk is not BrowserRisk.READ and not verified:
            event("browser_agent_approval_required", action=action.name, risk=action.risk.value,
                  url=action.url, legacy_approved_ignored=bool(legacy_approved))
            raise ApprovalRequired(action)
        if verified and action.risk is BrowserRisk.READ:
            raise ValueError("READ actions cannot consume an approval")
        if verified:
            arguments["_approval_verified"] = True
            action = replace(action, arguments=arguments)

        for adapter in self.adapters:
            try:
                if not adapter.available():
                    continue
                event("browser_agent_adapter_selected", adapter=adapter.adapter_id, action=action.name, risk=action.risk.value)
                result = adapter.execute(action)
            except Exception as exc:
                event("browser_agent_adapter_failed", adapter=getattr(adapter, "adapter_id", "unknown"), action=action.name, error=str(exc))
                if action.risk is not BrowserRisk.READ:
                    # The adapter may have committed the side effect before raising.
                    # Never replay an approved write through another adapter.
                    return BrowserActionResult("uncertain", "Результат действия неизвестен; автоматический повтор запрещён.")
                continue
            if action.risk is not BrowserRisk.READ:
                # Even an 'unavailable' response cannot prove that no side effect occurred.
                return result
            if result.status not in {"unavailable", "unsupported"}:
                return result

        return BrowserActionResult("unavailable", "Нет доступного browser-адаптера для этого действия.")
