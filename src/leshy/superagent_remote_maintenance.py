"""Typed remote maintenance for an explicitly requested SuperAgent ticket.

No LLM receives an SSH password, may choose shell commands or authorize a node.
Only a verified provider owner, registered pinned host, and owner-scoped
credential folder can launch the fixed Debian/Ubuntu package upgrade workflow.
A stopped/timeout task is NEVER silently retried after possible remote effects.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import time
from typing import Any, Callable

from .diagnostics import event, problem
from .module_settings import module_enabled
from .superagent_credential_settings import list_credential_file_names
from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_ssh_terminal import _registered_pin, verified_connection
from .superagent_verified_ssh import _credential, _fixed_command, inspect_remote_os


_IP4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_PACKAGES = re.compile(
    r"(?iu)(?:\b(?:обнови|обновить|обновление|обновляй|upgrade|apt-get|apt\s+upgrade)\b"
    r".{0,100}\b(?:пакет\w*|систем\w*|сервер\w*|ubuntu|debian)\b|"
    r"\b(?:пакет\w*|систем\w*|сервер\w*)\b.{0,100}"
    r"\b(?:обнови|обновить|обновление|upgrade)\b)"
)
_OS_QUESTION = re.compile(
    r"(?iu)\b(?:определи|определить|узнай|узнать|проверь|проверить|"
    r"какая|какой|что за)\b.{0,90}\b(?:ос|систем\w*|linux|windows|ubuntu|debian)\b"
)
_REMOTENESS = re.compile(r"(?iu)\b(?:сервер\w*|ssh|удаленн\w*|узл\w*|компьютер\w*)\b")
_TUTORIAL = re.compile(r"(?iu)\b(?:как|инструкция|расскажи|объясни)\b.{0,35}\bобновить\b")
_NEGATION = re.compile(r"(?iu)\bне\s+(?:обновляй|обновлять|запускай|выполняй)\b")
_FIXED_UPGRADE = (
    "/usr/bin/env DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get update "
    "&& /usr/bin/env DEBIAN_FRONTEND=noninteractive "
    "/usr/bin/apt-get -y -o Dpkg::Options::=--force-confdef "
    "-o Dpkg::Options::=--force-confold upgrade"
)
_FIXED_UPGRADABLE_COUNT = (
    "LC_ALL=C /usr/bin/apt list --upgradable 2>/dev/null "
    "| /usr/bin/grep -c 'upgradable from' || true"
)
_FIXED_DPKG_AUDIT = "/usr/bin/dpkg --audit | /usr/bin/wc -c"
_TIMEOUT_SECONDS = 1800


def maintenance_intent(text: str) -> str:
    """Only the *original user mandate* is considered, never tool/web text."""
    request = " ".join(str(text or "").split())[:12000]
    if _NEGATION.search(request) or _TUTORIAL.search(request):
        return ""
    if _PACKAGES.search(request) and (_REMOTENESS.search(request) or "apt" in request.casefold()):
        return "apt_upgrade"
    if _REMOTENESS.search(request) and _OS_QUESTION.search(request):
        return "inspect_os"
    return ""


def _actor_for_ticket(app: Any, owner_id: str) -> tuple[RemotePermissionPolicy, VerifiedActor]:
    """Recheck a real, live, provider-verified Web session within server SQLite.

    A model-supplied owner ID or an anonymous cookie alone is not sufficient.
    This helper is invoked only by the server's owner-scoped ticket executor.
    """
    identity = getattr(app, "owner_identity", None)
    if identity is None:
        raise PermissionError("Нет службы подтверждённых пользователей")
    policy = RemotePermissionPolicy(identity)
    with policy.storage.connection() as conn:
        session = conn.execute(
            "SELECT session_hash FROM owner_web_sessions "
            "WHERE owner_id=? AND expires_at>? "
            "ORDER BY last_seen_at DESC LIMIT 1",
            (owner_id, time.time()),
        ).fetchone()
    if session is None:
        raise PermissionError("Нет действующей подтверждённой Web-сессии владельца")
    actor = VerifiedActor(owner_id, str(session["session_hash"]), "web")
    policy._check_actor(actor)
    return policy, actor


def _choose_registered_node(policy: RemotePermissionPolicy, actor: VerifiedActor,
                            request: str) -> str:
    nodes: list[str] = []
    with policy.storage.connection() as conn:
        rows = conn.execute(
            "SELECT node_id FROM superagent_remote_nodes "
            "WHERE owner_id=? ORDER BY node_id LIMIT 65",
            (actor.owner_id,),
        ).fetchall()
        nodes = [str(r["node_id"]) for r in rows]
    if not nodes:
        raise PermissionError("Нет зарегистрированного SSH-сервера в профиле")
    requested: set[str] = set()
    for match in _IP4.finditer(request):
        try:
            requested.add(str(ipaddress.ip_address(match.group(0))))
        except ValueError:
            continue
    if len(requested) > 1:
        raise ValueError("В поручении указано несколько IP; нельзя выбирать сервер случайно")
    if requested:
        target = next(iter(requested))
        if target not in nodes:
            raise PermissionError("Указанный IP не зарегистрирован за владельцем")
        return target
    if len(nodes) != 1:
        raise ValueError("Зарегистрировано несколько серверов, но IP в поручении не указан")
    return nodes[0]


def _matching_credential(policy: RemotePermissionPolicy, actor: VerifiedActor,
                         node: str) -> str:
    """Look up a trusted JSON by *actual host field*; do not reveal secrets."""
    listed = list_credential_file_names(policy.storage, actor.owner_id)
    if not listed.get("available"):
        raise PermissionError("Доверенный каталог SSH не настроен или недоступен")
    if listed.get("truncated"):
        raise ValueError("Слишком много файлов доступа; нельзя выбирать неоднозначно")
    matched: list[str] = []
    for filename in listed.get("files", []):
        try:
            _credential(policy.storage, actor.owner_id, node, str(filename))
        except (OSError, ValueError, PermissionError):
            continue
        matched.append(str(filename))
        if len(matched) > 1:
            break
    if len(matched) != 1:
        raise PermissionError("Для сервера нет ровно одного подходящего файла в доверенном каталоге")
    return matched[0]


def _stream_remote_command(transport: Any, command: str, *,
                           stdin_password: str = "", timeout: int = _TIMEOUT_SECONDS) -> int:
    """Wait for process exit, draining output to prevent SSH deadlocks.

    Output is never returned to the model or logged. We do not terminate an
    in-flight dpkg transaction on a chat Stop: an abrupt kill can corrupt it.
    A disconnected transport or deadline means UNKNOWN, never success.
    """
    channel = transport.open_session(timeout=10)
    deadline = time.monotonic() + timeout
    try:
        channel.settimeout(10)
        channel.exec_command(command)
        if stdin_password:
            channel.sendall((stdin_password + "\n").encode("utf-8"))
        channel.shutdown_write()
        while time.monotonic() < deadline:
            progress = False
            while channel.recv_ready():
                raw = channel.recv(8192)
                if not raw:
                    break
                progress = True
            while channel.recv_stderr_ready():
                raw = channel.recv_stderr(8192)
                if not raw:
                    break
                progress = True
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                return int(channel.recv_exit_status())
            if not transport.is_active():
                raise ConnectionError("SSH-соединение прервано: состояние обновления неизвестно")
            if not progress:
                time.sleep(0.2)
        raise TimeoutError("Обновление превысило лимит: итог на сервере не подтверждён")
    finally:
        channel.close()


def _run_apt_update(policy: RemotePermissionPolicy, actor: VerifiedActor,
                    node: str, credentials: str,
                    *, still_active: Callable[[], bool]) -> dict[str, Any]:
    inspected = inspect_remote_os(policy, actor, node, credentials)
    family = str(inspected.get("os_family") or "")
    distro = str(inspected.get("distribution") or "")
    if family != "Linux" or distro.split(" ", 1)[0] not in {"ubuntu", "debian"}:
        return {
            "status": "blocked",
            "report": f"Определена ОС {family or 'неизвестна'} ({distro or 'дистрибутив не определён'}). "
                      "Команды apt автоматически не запускаю на неподтверждённой Debian/Ubuntu.",
        }
    if not still_active():
        return {"status": "blocked", "report": "Задание остановлено до начала изменения сервера."}
    login, password = _credential(policy.storage, actor.owner_id, node, credentials)
    pin = _registered_pin(policy, actor, node, "update")
    # The command is a constant compiled into Leshy, not model text.
    command = _FIXED_UPGRADE if login == "root" else (
        "sudo -S -p '' /bin/sh -c '" + _FIXED_UPGRADE + "'"
    )
    try:
        with verified_connection(node, credentials, pin, policy, actor, "update", 10) as (
            transport, observed
        ):
            policy.require(actor, node, "update", observed_fingerprint=observed)
            if not still_active():
                return {"status": "blocked", "report": "Задание остановлено до запуска apt."}
            event("superagent_apt_started", node_id=node)
            rc = _stream_remote_command(
                transport, command, stdin_password=password if login != "root" else "",
            )
    except (ConnectionError, TimeoutError, OSError) as exc:
        problem("superagent_apt_uncertain", type(exc).__name__, node_id=node)
        return {
            "status": "blocked",
            "report": f"Состояние обновления на {node} неизвестно после разрыва SSH или таймаута. "
                      "Автоматически повторять apt нельзя; сначала нужна проверка.",
        }
    if rc != 0:
        event("superagent_apt_failed", node_id=node, exit_code=rc)
        return {
            "status": "failed",
            "report": f"apt завершился на {node} с кодом {rc}. "
                      "Успешное обновление не подтверждено. "
                      "Возможны ошибка репозитория, блокировка dpkg или отказ sudo.",
        }
    # Verify from a *new* authenticated session after the mutation.
    try:
        with verified_connection(node, credentials, pin, policy, actor, "update", 10) as (
            transport, observed
        ):
            policy.require(actor, node, "update", observed_fingerprint=observed)
            code, line = _fixed_command(
                transport, _FIXED_UPGRADABLE_COUNT, 12.0, None,
            )
            audit_code, audit_bytes = _fixed_command(
                transport, _FIXED_DPKG_AUDIT, 12.0, None,
            )
        if code or not re.fullmatch(r"\d{1,6}", line):
            raise ValueError("Некорректный ответ apt")
        if audit_code or not re.fullmatch(r"\d{1,6}", audit_bytes):
            raise ValueError("Некорректный результат dpkg --audit")
        pending = int(line)
        incomplete = int(audit_bytes)
    except Exception as exc:
        problem("superagent_apt_postcheck_failed", type(exc).__name__, node_id=node)
        return {
            "status": "blocked",
            "report": f"apt на {node} завершился кодом 0, но независимая проверка "
                      "пакетов не удалась. Успех не подтверждаю.",
        }
    policy.storage.audit("superagent.apt.verified", {
        "node_id": node,
        "command_sha256": hashlib.sha256(_FIXED_UPGRADE.encode()).hexdigest(),
        "exit_code": rc,
        "pending_updates": pending,
        "dpkg_audit_bytes": incomplete,
    })
    if incomplete:
        return {
            "status": "blocked",
            "report": f"apt на {node} завершился кодом 0, но dpkg --audit "
                      "нашёл незавершённые операции. Сервер требует проверки.",
        }
    if pending:
        return {
            "status": "blocked",
            "report": f"apt на {node} завершился кодом 0; ОС {distro}; "
                      f"однако осталось {pending} доступных обновлений. "
                      "Полностью обновлённым сервер не считаю.",
        }
    return {
        "status": "ready",
        "report": f"Пакеты на {node} обновлены: apt завершился кодом 0; "
                  f"ОС {distro}; повторная проверка обнаружила 0 доступных "
                  "обычных обновлений. ESM Apps проверяется отдельно. "
                  "Перезагрузка сервера автоматически не выполнялась.",
    }


def run_remote_maintenance(app: Any, ticket: dict[str, Any],
                           *, still_active: Callable[[], bool]) -> tuple[str, str] | None:
    """Perform only the original owner's explicit typed maintenance request."""
    request = str(ticket.get("request_text") or "")
    intent = maintenance_intent(request)
    if not intent:
        return None
    owner = str(ticket.get("owner_id") or "").strip()
    if not module_enabled(app.storage, "remote-admin"):
        return "blocked", "Удалённое управление выключено в настройках Лешего. Никаких команд не выполнял."
    if not still_active():
        return "blocked", "Автономное задание остановлено до подключения."
    try:
        policy, actor = _actor_for_ticket(app, owner)
        node = _choose_registered_node(policy, actor, request)
        credential_file = _matching_credential(policy, actor, node)
        if intent == "inspect_os":
            os_info = inspect_remote_os(policy, actor, node, credential_file)
            if not os_info.get("verified"):
                return "blocked", f"Удалённая ОС сервера {node} не подтверждена."
            return "ready", (
                f"По проверенному SSH-подключению сервер {node}: "
                f"{os_info.get('os_family')} {os_info.get('distribution') or ''}. "
                "Результат получен с зарегистрированного узла, не угадан."
            )
        outcome = _run_apt_update(
            policy, actor, node, credential_file, still_active=still_active,
        )
        return str(outcome["status"]), str(outcome["report"])
    except (PermissionError, ValueError, FileNotFoundError) as exc:
        # These are expected setup/auth problems, NOT prompts for the user to
        # execute diagnostics. No password or trusted file contents are logged.
        return "blocked", f"Не выполнено: {exc}. SSH-обновление не подтверждено."
    except Exception as exc:
        problem("superagent_remote_maintenance_error", type(exc).__name__)
        return "blocked", (
            "Ошибка проверенного SSH-исполнителя; состояние изменений неизвестно. "
            "Самостоятельно повторять потенциально изменившую сервер команду нельзя."
        )


__all__ = ["maintenance_intent", "run_remote_maintenance"]
