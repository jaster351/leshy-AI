from __future__ import annotations

import re
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
    # Ticket drafting is not an instruction to scan every IP in the client's quote.
    # Explicit /support and "answer the client" take precedence over IP diagnostics.
    value = " ".join(str(text or "").casefold().replace("ё", "е").split())
    if ("наименование в панели" in value and "обработчик" in value):
        return False
    if value.startswith(("/support", "/поддержка")) or any(
        marker in value for (
            "ответь клиенту", "напиши ответ клиенту", "составь ответ клиенту",
            "сообщение клиента", "вопрос клиента",
        )
    ):
        return False
    try:
        return _module(app).checker.looks_like_request(text)
    except Exception:
        return False



# Match short clarifying questions only. Never re-interpret a new target as a follow-up.
_FOLLOWUP_MARKERS = (
    "как ты это понял", "как это понял", "откуда ты это взял",
    "откуда информация", "почему ты так решил", "почему решил",
    "почему заблокирован", "чем подтверждается", "какие доказательства",
    "какие проверки", "что ты проверял", "что проверял",
    "покажи диагностику", "покажи результаты", "покажи проверки",
    "покажи логи проверки", "объясни проверку", "объясни вывод",
    "на основании чего", "как проверял", "как ты проверил",
    "каким способом проверил", "распиши механизм", "как определил блокировку",
)


def _is_diagnostic_followup(message: str) -> bool:
    normalized = " ".join(str(message or "").lower().replace("ё", "е").split())
    if not normalized or len(normalized) > 220:
        return False
    return any(phrase in normalized for phrase in _FOLLOWUP_MARKERS)


def _previous_diagnostic(app, owner_id: str, session_id: str | None) -> str | None:
    """Read only the immediately preceding assistant response in this owner's session.

    This does not rerun probes or consult the LLM: the answer must be grounded in
    evidence that was actually saved during the preceding diagnostic request.
    """
    if not session_id:
        return None
    with app.storage.connection() as conn:
        session = conn.execute(
            "SELECT id FROM sessions WHERE id=? AND owner_id=?",
            (session_id, owner_id),
        ).fetchone()
        if session is None:
            return None
        row = conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if row is None or row["role"] != "assistant":
        return None
    answer = str(row["content"] or "")
    # Both the older short-answer implementation and the new evidence-first
    # implementation may be present in persistent user history.
    legacy = bool(re.search(
        r"(?m)^\d{1,3}(?:\.\d{1,3}){3}: IP заблокирован ТСПУ",
        answer,
    ))
    modern = bool(re.search(
        r"(?m)^IP-адрес \S+\. Блокировка ТСПУ не подтверждена\.",
        answer,
    )) and "Проверка " in answer
    confirmed = bool(re.search(
        r"(?m)^IP-адрес \S+ заблокирован ТСПУ\.",
        answer,
    )) or "подтверждена по 3/3 признакам" in answer
    if not (legacy or modern or confirmed):
        return None
    return answer


def _explain_saved_diagnostic(previous: str) -> str:
    """Replay previous measured results or explicitly admit missing evidence."""
    marker = previous.find("\n\nПроверка ")
    if "подтверждена по 3/3 признакам" in previous:
        return (
            "Предыдущая проверка получила три согласованных признака: "
            "тайм-аут прямого TCP, тайм-аут Check-Host из РФ на том же порту "
            "и завершённый Cheburcheck со статусом tspu_block. "
            "Это диагностическое подтверждение по принятой методике, "
            "а не прямое наблюдение на оборудовании ТСПУ. "
            "Новые проверки для этого пояснения не запускались.\n\n" + previous
        )
    if marker >= 0 and "Запуски инструментов (ID;" in previous:
        report = previous[marker:].strip()
        return (
            "Механизм проверки: Леший выполняет TCP Connect к портам 22 и 3389 "
            "со своего сервера, запрашивает Чебурчек (список и распределённый Probe), "
            "затем Check-Host из российских и зарубежных узлов. "
            "Далее он сопоставляет доступность портов и независимые сигналы. "
            "Ни тайм-аут, ни совпадение нескольких ответов сами по себе не доказывают "
            "фильтрацию именно ТСПУ. Ниже — фактические данные предыдущего запуска. "
            "Для этого пояснения я не выполнял новую проверку и не использовал "
            "поисковые системы. Отдельный индекс признаков не является "
            "доказательством причины ТСПУ.\n\n"
            + report
        )
    return (
        "Предыдущий ответ о блокировке ТСПУ нельзя достоверно обосновать: "
        "в истории этого диалога нет полного протокола TCP Connect, Чебурчека "
        "и Check-Host с результатами по узлам и подтверждениями выполнения. "
        "Утверждение «100% ТСПУ» без этих доказательств недопустимо. "
        "Я не буду придумывать команды, результаты поиска или объяснение "
        "задним числом. Отправьте IP снова для новой проверки с полным отчётом."
    )

def _service_label(metadata: dict[str, Any]) -> str:
    profile = str(metadata.get("profile") or "").strip()
    ports = [int(value) for value in (metadata.get("service_ports") or []) if str(value).isdigit()]
    if profile == "Linux/SSH" or ports == [22]:
        return "Linux/SSH, порт 22"
    if profile == "Windows/RDP" or ports == [3389]:
        return "Windows/RDP, порт 3389"
    if set(ports) == {22, 3389}:
        return "SSH/22 и RDP/3389"
    return "тип сервера не определён"


def _compact_answer(metadata: dict[str, Any]) -> str:
    """Only a separately verified TSPU attribution permits a copyable categorical conclusion.

    Legacy probability/heuristic scores are never proof, even when equal to 100.
    """
    target = str(metadata.get("target") or "").strip()
    if target and metadata.get("blocking_attribution") == "confirmed_three_signals":
        ports = ", ".join(str(p) for p in metadata.get("matched_ports") or [])
        remote = metadata.get("remote") or []
        russian = [
            row for row in remote if isinstance(row, dict)
            and str(row.get("country_code") or "").lower() == "ru"
            and row.get("port") in (metadata.get("matched_ports") or [])
        ]
        ru_timeouts = sum(row.get("status") == "timeout" for row in russian)
        chebur = metadata.get("chebur_probe") or {}
        observations = chebur.get("observations") or []
        blocked_probes = sum(
            "tspu_block" in (row.get("verdicts") or [])
            for row in observations if isinstance(row, dict)
        )
        port_lines = []
        for port in (22, 3389):
            direct = next(
                (row.get("status") for row in metadata.get("local") or []
                 if isinstance(row, dict) and row.get("port") == port),
                "нет измерения",
            )
            rows = [
                row for row in remote if isinstance(row, dict) and row.get("port") == port
            ]
            ru = [row for row in rows if str(row.get("country_code") or "").lower() == "ru"]
            abroad = [row for row in rows if row not in ru]
            port_lines.append(
                f"TCP/{port}: локально {direct}; Check-Host РФ: "
                f"{sum(row.get('status') == 'connected' for row in ru)}/{len(ru)} подключений, "
                f"{sum(row.get('status') == 'timeout' for row in ru)} timeout; "
                f"зарубежные: {sum(row.get('status') == 'connected' for row in abroad)}/{len(abroad)}."
            )
        return (
            f"IP-адрес {target}: блокировка ТСПУ подтверждена по 3/3 признакам.\n"
            + "\n".join(port_lines) + "\n"
            + f"Совпадение на TCP/{ports}: РФ {ru_timeouts} timeout из {len(russian)} проверок.\n"
            + f"Cheburcheck: tspu_block ({blocked_probes}/{len(observations)} сканеров); "
            "проверка завершена."
        )
    if (
        target
        and metadata.get("blocking_attribution") == "confirmed_tspu"
        and metadata.get("proof_verification") == "verified"
        and str(metadata.get("proof_record_id") or "").strip()
    ):
        return f"IP-адрес {target} заблокирован ТСПУ."
    return f"IP-адрес {target}. Блокировка ТСПУ не подтверждена." if target else "Блокировка ТСПУ не подтверждена."


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
    if chebur.get("ok") and source_url.startswith("https://cheburcheck.ru/"):
        sources.append({"title": "Cheburcheck", "url": source_url})
    check_host_reports = sorted({
        str(row.get("source_url") or "") for row in response.metadata.get("remote") or []
        if isinstance(row, dict)
        and row.get("status") in {"connected", "refused", "timeout"}
        and str(row.get("source_url") or "").startswith("https://check-host.net/")
    })
    for url in check_host_reports:
        sources.append({"title": "Результаты Check-Host", "url": url})
    summary = _compact_answer(response.metadata)
    is_three_signal = response.metadata.get("blocking_attribution") == "confirmed_three_signals"
    if not is_three_signal and (
        response.metadata.get("blocking_attribution") != "confirmed_tspu"
        or not response.metadata.get("proof_record_id")
        or response.metadata.get("proof_verification") != "verified"
    ):
        # The complete diagnostic report remains visible when no full conclusion is possible.
        summary += "\n\n" + response.text
    response.metadata["copyable_conclusion"] = (
        summary if is_three_signal or (
            response.metadata.get("blocking_attribution") == "confirmed_tspu"
            and response.metadata.get("proof_verification") == "verified"
            and response.metadata.get("proof_record_id")
        ) else ""
    )
    return summary, sources, response.metadata


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
        owner = (owner_id or "web:local").strip()[:200]
        followup = (
            _previous_diagnostic(self.app, owner, session_id)
            if _is_diagnostic_followup(value) else None
        )
        if followup is None and not _matches(self.app, value):
            yield from _ORIGINAL_STREAM(
                self,
                value,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

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
                "text": (
                    "Поднимаю сохранённый протокол проверки…"
                    if followup is not None
                    else "Проверяю доступность IP и признаки блокировки ТСПУ…"
                ),
            })
            if followup is not None:
                answer, sources, metadata = _explain_saved_diagnostic(followup), [], {
                    "network_blocking_followup": True,
                    "new_probes_executed": False,
                    "blocking_attribution": "unconfirmed",
                }
            else:
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
        owner = (owner_id or "web:local").strip()[:200]
        followup = (
            _previous_diagnostic(self, owner, session_id)
            if _is_diagnostic_followup(value) else None
        )
        if followup is None and not _matches(self, value):
            return _ORIGINAL_CHAT(self, value, owner_id=owner_id, session_id=session_id, **kwargs)
        sid = _save_user(self, value, owner, session_id)
        if followup is not None:
            answer, sources, metadata = _explain_saved_diagnostic(followup), [], {
                "network_blocking_followup": True,
                "new_probes_executed": False,
                "blocking_attribution": "unconfirmed",
            }
        else:
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
