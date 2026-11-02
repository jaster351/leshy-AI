from __future__ import annotations

import time
from typing import Any, Iterator

from .diagnostics import event, problem
from .module_settings import module_enabled
from .modules import ModuleRequest
from .network_blocking_module import NetworkBlockingModule
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow


_INSTALLED = False
_ORIGINAL_STREAM = None
_ORIGINAL_CHAT = None


def _module(app) -> NetworkBlockingModule:
    module = getattr(app, "network_blocking_module", None)
    if isinstance(module, NetworkBlockingModule):
        return module
    module = NetworkBlockingModule()
    app.network_blocking_module = module
    return module


def _enabled(app) -> bool:
    try:
        return module_enabled(app.storage, "network-blocking")
    except Exception as exc:
        problem("network_blocking_setting_failed", str(exc))
        return True


def _matches(app, text: str) -> bool:
    if not _enabled(app):
        return False
    try:
        return _module(app).checker.looks_like_request(text)
    except Exception:
        return False


def _service_text(metadata: dict[str, Any]) -> str:
    profile = str(metadata.get("profile") or "Не определён")
    ports = [int(value) for value in (metadata.get("service_ports") or []) if str(value).isdigit()]
    if profile == "Linux/SSH" or ports == [22]:
        return "Сервер определяется как Linux/SSH, рабочий сервис — TCP/22."
    if profile == "Windows/RDP" or ports == [3389]:
        return "Сервер определяется как Windows/RDP, рабочий сервис — TCP/3389."
    if set(ports) == {22, 3389}:
        return "На сервере доступны и SSH/22, и RDP/3389; ОС по портам однозначно не определяется."
    return "Рабочий сервис по TCP/22 и TCP/3389 однозначно не определён."


def _compact_answer(metadata: dict[str, Any]) -> str:
    """Пользовательский вывод: диагноз + основание + действие, без сырого дампа probe-узлов."""
    target = str(metadata.get("target") or "").strip()
    probability = max(0, min(100, int(metadata.get("tspu_probability") or 0)))
    profile_text = _service_text(metadata)
    local = list(metadata.get("local") or [])
    remote = list(metadata.get("remote") or [])
    probe = metadata.get("chebur_probe") or {}
    chebur = metadata.get("cheburcheck") or {}

    official_tspu = any(
        "tspu_block" in [str(value) for value in (item.get("verdicts") or [])]
        for item in (probe.get("observations") or [])
        if isinstance(item, dict)
    )
    geographic_tspu = False
    for port in (22, 3389):
        rows = [item for item in remote if isinstance(item, dict) and int(item.get("port") or 0) == port]
        ru_failed = any(
            str(item.get("country_code") or "").lower() == "ru"
            and str(item.get("status") or "") in {"timeout", "error"}
            for item in rows
        )
        foreign_ok = any(
            str(item.get("country_code") or "").lower() not in {"", "ru"}
            and str(item.get("status") or "") == "connected"
            for item in rows
        )
        if ru_failed and foreign_ok:
            geographic_tspu = True
            break

    local_timeout_ports = sorted(
        int(item.get("port") or 0)
        for item in local
        if isinstance(item, dict) and str(item.get("status") or "") == "timeout" and int(item.get("port") or 0) in {22, 3389}
    )

    prefix = f"{target}: " if target else ""
    if probability >= 100:
        answer = [f"{prefix}IP с высокой вероятностью заблокирован средствами ТСПУ.", profile_text]
        evidence: list[str] = []
        if official_tspu:
            evidence.append("распределённый Cheburcheck Probe массово возвращает `tspu_block`")
        if geographic_tspu:
            evidence.append("зарубежные точки подключаются, а часть российских получает timeout")
        if local_timeout_ports:
            evidence.append("из сети Лешего соединение с рабочим портом не устанавливается")
        if evidence:
            answer.append("Основание: " + "; ".join(evidence) + ".")
        answer.append("Решение: сменить или выдать серверу другой IP-адрес и повторить проверку нового IP.")
        return "\n".join(answer)

    if probability >= 70:
        answer = [f"{prefix}есть сильные признаки блокировки или попадания IP в блокировочный диапазон.", profile_text]
        if bool(chebur.get("blocked")):
            answer.append("Cheburcheck также отмечает адрес или его диапазон как заблокированный.")
        answer.append("Решение: повторить проверку; если результат сохраняется — сменить IP и проверить новый адрес.")
        return "\n".join(answer)

    if probability >= 30:
        return "\n".join([
            f"{prefix}однозначная блокировка ТСПУ не подтверждена, но есть косвенные признаки.",
            profile_text,
            "Решение: повторить проверку позже и отдельно проверить маршрут/фаервол. IP менять только если географический признак повторяется.",
        ])

    if any(isinstance(item, dict) and str(item.get("status") or "") == "connected" for item in remote):
        return "\n".join([
            f"{prefix}признаки блокировки ТСПУ не подтверждены.",
            profile_text,
            "Если подключение не работает именно из Вашей сети, проверяйте локальный маршрут, фаервол или провайдера — сам IP извне доступен.",
        ])

    return "\n".join([
        f"{prefix}данных недостаточно для уверенного вывода о ТСПУ.",
        profile_text,
        "Решение: повторить проверку. Если 22 и 3389 не отвечают ни из России, ни из других стран, сначала проверять сам сервер и его фаервол.",
    ])


def _run(app, text: str) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    response = _module(app).handle(
        ModuleRequest(
            text=text,
            channel="chat",
            metadata={"event": "network_blocking_check"},
        )
    )
    if not response.handled:
        raise ValueError("Не удалось определить IP-адрес или домен для проверки")
    chebur = response.metadata.get("cheburcheck") or {}
    sources: list[dict[str, str]] = []
    source_url = str(chebur.get("source_url") or "")
    if source_url:
        sources.append({"title": "Cheburcheck", "url": source_url})
    sources.append({"title": "Check-Host", "url": "https://check-host.net/check-tcp"})
    return _compact_answer(response.metadata), sources, response.metadata


def _save_user(app, message: str, owner_id: str, session_id: str | None) -> str:
    sid = app._ensure_session(owner_id, session_id)
    now = utcnow()
    with app.storage.connection() as conn:
        conn.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
            (sid, "user", message, now),
        )
        count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
        if count == 1:
            conn.execute(
                "UPDATE sessions SET title=?,title_source=?,updated_at=? WHERE id=?",
                (" ".join(message.split())[:80], "auto", now, sid),
            )
    return sid


def install_network_blocking_routing(app_class, app=None) -> None:
    """Подключить проверку IP как отдельный fast-path до общей LLM."""
    global _INSTALLED, _ORIGINAL_STREAM, _ORIGINAL_CHAT
    if app is not None:
        _module(app)
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_CHAT = app_class.chat

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        value = str(message or "").strip()
        if not _matches(self.app, value):
            yield from _ORIGINAL_STREAM(
                self,
                value,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

        owner = (owner_id or "web:local").strip()[:200]
        rid, cancel = self._register(request_id)
        sid = ""
        started = time.monotonic()
        try:
            sid, _rows = self._save_user_and_history(value, owner, session_id)
            yield StreamEvent("start", {
                "request_id": rid,
                "session_id": sid,
                "network_blocking": True,
            })
            if cancel.is_set():
                yield StreamEvent("done", {
                    "request_id": rid,
                    "session_id": sid,
                    "answer": "",
                    "sources": [],
                    "stopped": True,
                    "network_blocking": True,
                })
                return

            yield StreamEvent("activity", {
                "kind": "network_blocking",
                "phase": "local_tcp",
                "text": "Проверяю доступность IP и признаки блокировки ТСПУ…",
            })
            answer, sources, metadata = _run(self.app, value)
            if cancel.is_set():
                return
            for chunk in self._chunk_fallback(answer, 190):
                if cancel.is_set():
                    break
                yield StreamEvent("delta", {"text": chunk})
            stopped = cancel.is_set()
            answered_at = self.app._save_assistant(sid, answer) if answer else utcnow()
            event(
                "network_blocking_chat_answered",
                request_id=rid,
                session_id=sid,
                owner_id=owner,
                target=str(metadata.get("target") or ""),
                tspu_probability=int(metadata.get("tspu_probability") or 0),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": answered_at,
                "sources": sources,
                "stopped": stopped,
                "network_blocking": True,
                "network_blocking_result": metadata,
                "latency_ms": int((time.monotonic() - started) * 1000),
            })
        except Exception as exc:
            problem("network_blocking_chat_failed", str(exc), request_id=rid, session_id=sid, owner_id=owner)
            yield StreamEvent("error", {
                "request_id": rid,
                "session_id": sid,
                "error": str(exc),
                "network_blocking": True,
            })
        finally:
            self._unregister(rid)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None, **kwargs) -> dict[str, Any]:
        value = str(message or "").strip()
        if not _matches(self, value):
            return _ORIGINAL_CHAT(self, value, owner_id=owner_id, session_id=session_id, **kwargs)
        owner = (owner_id or "web:local").strip()[:200]
        sid = _save_user(self, value, owner, session_id)
        answer, sources, metadata = _run(self, value)
        created_at = self._save_assistant(sid, answer)
        event(
            "network_blocking_chat_answered",
            session_id=sid,
            owner_id=owner,
            target=str(metadata.get("target") or ""),
            tspu_probability=int(metadata.get("tspu_probability") or 0),
        )
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": created_at,
            "sources": sources,
            "network_blocking": True,
            "network_blocking_result": metadata,
        }

    RealtimeChatManager.stream = stream
    app_class.chat = chat


__all__ = ["install_network_blocking_routing"]
