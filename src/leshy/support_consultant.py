from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import socket
from typing import Protocol

from .llm import ChatMessage, LLM
from .logging_paths import central_log_path
from .modules import ModuleManifest, ModuleRequest, ModuleResponse
from .work_schedule import WorkAssistancePolicy, parse_event_time, truthy


DEFAULT_SERVICE_PORTS = (22, 3389)


@dataclass(frozen=True, slots=True)
class TicketNotice:
    """Уведомление о новой или обновлённой заявке."""

    ticket_id: str
    client_id: str = ""
    email: str = ""
    question: str = ""


@dataclass(frozen=True, slots=True)
class TicketMessage:
    """Одно сообщение из истории заявки."""

    author: str
    text: str
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class ClientTicketContext:
    """Контекст клиента, заявки и связанного сервиса."""

    ticket_id: str
    client_id: str
    email: str
    subject: str = ""
    service_name: str = ""
    server_host: str = ""
    server_ip: str = ""
    operating_system: str = ""
    ports: tuple[int, ...] = ()
    messages: tuple[TicketMessage, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    """Результат одной безопасной проверки доступности."""

    name: str
    status: str
    details: str


class ReadOnlyTicketSource(Protocol):
    """Источник данных заявок, который предоставляет только операции чтения."""

    def load_context(self, notice: TicketNotice) -> ClientTicketContext: ...


class DiagnosticProvider(Protocol):
    """Набор диагностик, выполняемых до подготовки ответа."""

    def run(self, context: ClientTicketContext) -> list[DiagnosticResult]: ...


class TcpAvailabilityDiagnostics:
    """Быстрая TCP-проверка сервера без shell-команд и изменения удалённой системы.

    Для определения сервисного профиля Леший всегда проверяет SSH/22 и RDP/3389
    параллельно и только после этого делает вывод. Поле operating_system не
    используется для выбора порта: ОС/профиль выводятся из фактической доступности.
    """

    def __init__(self, timeout_seconds: float = 5.0):
        self.timeout_seconds = max(0.2, float(timeout_seconds))

    def _ports(self, context: ClientTicketContext) -> tuple[int, ...]:
        extra: list[int] = []
        for port in context.ports:
            try:
                value = int(port)
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 65535:
                extra.append(value)
        return tuple(dict.fromkeys((*DEFAULT_SERVICE_PORTS, *extra)))

    def _check_port(self, target: str, port: int) -> DiagnosticResult:
        try:
            with socket.create_connection((target, port), timeout=self.timeout_seconds):
                return DiagnosticResult(f"TCP/{port}", "доступен", "Соединение установлено")
        except TimeoutError:
            return DiagnosticResult(
                f"TCP/{port}",
                "тайм-аут",
                f"Соединение не установлено за {self.timeout_seconds:g} с",
            )
        except OSError as exc:
            return DiagnosticResult(f"TCP/{port}", "недоступен", str(exc))

    @staticmethod
    def _service_profile(results: list[DiagnosticResult]) -> DiagnosticResult:
        statuses = {item.name: item.status for item in results}
        ssh_open = statuses.get("TCP/22") == "доступен"
        rdp_open = statuses.get("TCP/3389") == "доступен"

        if ssh_open and not rdp_open:
            return DiagnosticResult(
                "Профиль сервера",
                "Linux/SSH",
                "TCP/22 отвечает, TCP/3389 не отвечает; профиль определён по фактической доступности сервиса.",
            )
        if rdp_open and not ssh_open:
            return DiagnosticResult(
                "Профиль сервера",
                "Windows/RDP",
                "TCP/3389 отвечает, TCP/22 не отвечает; профиль определён по фактической доступности сервиса.",
            )
        if ssh_open and rdp_open:
            return DiagnosticResult(
                "Профиль сервера",
                "неоднозначный",
                "Одновременно отвечают TCP/22 и TCP/3389; доступны оба сервиса, поэтому ОС по портам однозначно не определяется.",
            )
        return DiagnosticResult(
            "Профиль сервера",
            "не определён",
            "Ни TCP/22, ни TCP/3389 не подтвердили доступность; ОС по недоступным портам не угадывается.",
        )

    def run(self, context: ClientTicketContext) -> list[DiagnosticResult]:
        target = (context.server_ip or context.server_host).strip()
        if not target:
            return [DiagnosticResult("TCP", "нет данных", "В заявке не найден адрес сервера")]

        ports = self._ports(context)
        workers = max(1, min(len(ports), 8))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="leshy-tcp-check") as pool:
            results = list(pool.map(lambda port: self._check_port(target, port), ports))
        results.append(self._service_profile(results))
        return results


@dataclass(slots=True)
class SupportConsultantModule:
    """Консультант: история → диагностика → варианты ответа владельцу."""

    source: ReadOnlyTicketSource
    diagnostics: DiagnosticProvider
    llm: LLM
    brain_dir: Path | None = None
    work_policy: WorkAssistancePolicy = field(default_factory=WorkAssistancePolicy)
    manifest: ModuleManifest = field(
        default_factory=lambda: ModuleManifest(
            name="support-consultant",
            version="0.2.0",
            description="Собирает контекст заявки, выполняет проверки и готовит варианты ответа",
            priority=500,
        )
    )

    def can_handle(self, request: ModuleRequest) -> bool:
        return str(request.metadata.get("event") or "") == "support_ticket_notice"

    @staticmethod
    def _notice(request: ModuleRequest) -> TicketNotice:
        return TicketNotice(
            ticket_id=str(request.metadata.get("ticket_id") or "").strip(),
            client_id=str(request.metadata.get("client_id") or "").strip(),
            email=str(request.metadata.get("email") or "").strip(),
            question=str(request.metadata.get("question") or request.text or "").strip(),
        )

    def _work_assistance_allowed(self, request: ModuleRequest) -> bool:
        return self.work_policy.allows(
            now=parse_event_time(request.metadata.get("event_time")),
            workstation_active=truthy(request.metadata.get("workstation_active")),
            manual_work_mode=truthy(request.metadata.get("manual_work_mode")),
        )

    @staticmethod
    def _identity_conflict(notice: TicketNotice, context: ClientTicketContext) -> str:
        if notice.client_id and context.client_id and notice.client_id != context.client_id:
            return "ID клиента из уведомления не совпал с данными BILLmanager"
        if notice.email and context.email and notice.email.casefold() != context.email.casefold():
            return "E-mail из уведомления не совпал с данными BILLmanager"
        return ""

    def _audit(self, ticket_id: str, diagnostics: list[DiagnosticResult], *, conflict: bool) -> None:
        if self.brain_dir is None:
            return
        path = central_log_path(self.brain_dir, "support-consultant.jsonl")
        record = {
            "event": "support_consultation",
            "ticket_ref": sha256(ticket_id.encode("utf-8")).hexdigest()[:12] if ticket_id else "unknown",
            "conflict": conflict,
            "diagnostics": [{"name": item.name, "status": item.status} for item in diagnostics],
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _history_text(context: ClientTicketContext, limit: int = 24) -> str:
        rows: list[str] = []
        for message in context.messages[-limit:]:
            when = f" [{message.created_at}]" if message.created_at else ""
            rows.append(f"{message.author}{when}: {message.text}")
        return "\n".join(rows) if rows else "История отсутствует."

    @staticmethod
    def _diagnostics_text(results: list[DiagnosticResult]) -> str:
        return "\n".join(f"- {item.name}: {item.status}. {item.details}" for item in results) or "Проверки не выполнялись."

    def handle(self, request: ModuleRequest) -> ModuleResponse:
        if not self._work_assistance_allowed(request):
            return ModuleResponse(
                handled=True,
                text="",
                metadata={"status": "work_assistance_suppressed", "notify": False, "read_only": True},
            )

        notice = self._notice(request)
        if not notice.ticket_id and not notice.client_id and not notice.email:
            return ModuleResponse(
                handled=True,
                text="Не удалось определить заявку или клиента из уведомления. Нужны ticket_id, client_id или e-mail.",
                metadata={"status": "insufficient_identity"},
            )

        context = self.source.load_context(notice)
        conflict = self._identity_conflict(notice, context)
        if conflict:
            self._audit(context.ticket_id or notice.ticket_id, [], conflict=True)
            return ModuleResponse(
                handled=True,
                text=f"Остановил подготовку ответа: {conflict}. Нужна ручная проверка личности клиента.",
                metadata={"status": "identity_conflict"},
            )

        diagnostic_results = self.diagnostics.run(context)
        self._audit(context.ticket_id or notice.ticket_id, diagnostic_results, conflict=False)

        prompt = (
            "Ты работаешь как внутренний консультант технической поддержки. "
            "Ничего не отправляй клиенту и не предлагай совершать изменения в BILLmanager. "
            "Сначала опирайся на историю переписки и результаты фактической диагностики. "
            "Не спрашивай операционную систему, если профиль уже можно определить по ответу TCP/22 и TCP/3389. "
            "Подготовь три коротких варианта ответа специалисту: основной, более краткий и осторожный при недостатке данных. "
            "Не выдумывай выполненные проверки и факты. Пиши естественно, без канцелярита.\n\n"
            f"Тема: {context.subject or 'не указана'}\n"
            f"Сервис: {context.service_name or 'не указан'}\n"
            f"Текущий вопрос клиента: {notice.question or 'не указан'}\n\n"
            f"История:\n{self._history_text(context)}\n\n"
            f"Диагностика:\n{self._diagnostics_text(diagnostic_results)}"
        )
        answer = self.llm.chat(
            [
                ChatMessage("system", "Ты — локальный помощник Леший. Отвечай только на русском языке."),
                ChatMessage("user", prompt),
            ]
        ).strip()

        return ModuleResponse(
            handled=True,
            text=answer,
            metadata={
                "status": "prepared",
                "ticket_id": context.ticket_id or notice.ticket_id,
                "diagnostics": [
                    {"name": item.name, "status": item.status, "details": item.details}
                    for item in diagnostic_results
                ],
                "read_only": True,
                "notify": True,
            },
        )
