from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
import ipaddress
import json
import re
import socket
import time
from typing import Any
from urllib import parse, request

from .diagnostics import event, problem
from .modules import ModuleManifest, ModuleRequest, ModuleResponse
from .verified_tool_receipts import VerifiedToolReceipts, ToolReceipt


DEFAULT_PORTS = (22, 3389)
CHEBURCHECK_ENDPOINT = "https://cheburcheck.ru/api/v1/check"
CHEBURCHECK_PROBE_ENDPOINT = "https://cheburcheck.ru/api/v1/probe/{query_id}"
CHECK_HOST_NODES_ENDPOINT = "https://check-host.net/nodes/hosts"
CHECK_HOST_START_ENDPOINT = "https://check-host.net/check-tcp"
CHECK_HOST_RESULT_ENDPOINT = "https://check-host.net/check-result/{request_id}"

_IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_HOST_RE = re.compile(r"(?<![A-Za-z0-9.-])([A-Za-z0-9](?:[A-Za-z0-9.-]{1,251}[A-Za-z0-9])?\.[A-Za-z]{2,63})(?![A-Za-z0-9.-])")


@dataclass(frozen=True, slots=True)
class TcpObservation:
    source: str
    port: int
    status: str
    country_code: str = ""
    country: str = ""
    city: str = ""
    node: str = ""
    latency_ms: int | None = None
    address: str = ""
    error: str = ""
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class CheburcheckEvidence:
    ok: bool
    blocked: bool | None = None
    target: str = ""
    target_type: str = ""
    query_id: str = ""
    rkn_domain: str = ""
    blocked_subnets: tuple[str, ...] = ()
    cdn_providers: tuple[str, ...] = ()
    whitelisted: bool = False
    source_url: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class CheburProbeObservation:
    probe_id: str
    region: str = ""
    provider: str = ""
    asn: str = ""
    verdicts: tuple[str, ...] = ()
    target_hop: int | None = None
    dpi_hop: int | None = None


@dataclass(frozen=True, slots=True)
class CheburProbeSummary:
    ok: bool
    online_probes: int = 0
    responded_probes: int = 0
    observations: tuple[CheburProbeObservation, ...] = ()
    error: str = ""


@dataclass(frozen=True, slots=True)
class BlockingAssessment:
    target: str
    profile: str
    service_ports: tuple[int, ...]
    tspu_probability: int
    verdict: str
    availability_percent: int
    local: tuple[TcpObservation, ...]
    remote: tuple[TcpObservation, ...]
    cheburcheck: CheburcheckEvidence
    chebur_probe: CheburProbeSummary
    reasons: tuple[str, ...]
    checked_at_epoch: float
    tool_receipts: tuple[ToolReceipt, ...] = ()


class NetworkBlockingChecker:
    """Отдельная диагностика IP/доменов и вероятной фильтрации ТСПУ.

    Последовательность соответствует согласованной схеме:
    1. сначала TCP connect (эквивалент Telnet-проверки доступности) из сети Лешего
       одновременно на SSH/22 и RDP/3389;
    2. затем публичный Cheburcheck `/api/v1/check` для реестров РКН/CDN;
    3. по подписанному query id запускается официальный `/api/v1/probe/<id>` SSE,
       который использует сайт: распределённые probe-узлы и TCP traceroute после DPI;
    4. параллельно выполняется независимая TCP-проверка Check-Host из российских
       и зарубежных точек.

    PROBE_ID/PROBE_TOKEN не нужны: без token Cheburcheck использует публичные
    видимые probe-узлы. Токен нужен только для адресного собственного probe.
    """

    def __init__(
        self,
        *,
        local_timeout_seconds: float = 5.0,
        http_timeout_seconds: float = 12.0,
        check_host_wait_seconds: float = 12.0,
        chebur_probe_wait_seconds: float = 125.0,
    ) -> None:
        self.local_timeout_seconds = max(0.5, min(float(local_timeout_seconds), 30.0))
        self.http_timeout_seconds = max(3.0, min(float(http_timeout_seconds), 30.0))
        self.check_host_wait_seconds = max(3.0, min(float(check_host_wait_seconds), 30.0))
        self.chebur_probe_wait_seconds = max(10.0, min(float(chebur_probe_wait_seconds), 135.0))
        self._nodes_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})
        self._receipts = VerifiedToolReceipts()

    @staticmethod
    def normalize_target(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("IP-адрес или домен не указан")
        if "://" in raw:
            raw = parse.urlparse(raw).hostname or raw
        raw = raw.strip().strip("[]").rstrip(".")
        try:
            ip = ipaddress.ip_address(raw)
            if not ip.is_global:
                raise ValueError("Диагностика локальных, служебных и частных IP-адресов запрещена")
            return str(ip)
        except ValueError as exc:
            if "запрещена" in str(exc):
                raise
            pass
        if len(raw) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", raw) or "." not in raw:
            raise ValueError("Некорректный IP-адрес или домен")
        return raw.lower()

    @staticmethod
    def extract_target(text: str) -> str:
        raw = str(text or "").strip()
        for candidate in _IPV4_RE.findall(raw):
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
        for token in re.split(r"\s+", raw):
            cleaned = token.strip("[](){}<>,;!?'\"")
            if ":" in cleaned and cleaned.count(":") >= 2:
                try:
                    return str(ipaddress.ip_address(cleaned))
                except ValueError:
                    pass
        match = _HOST_RE.search(raw)
        return match.group(1).lower() if match else ""

    @classmethod
    def looks_like_request(cls, text: str) -> bool:
        raw = " ".join(str(text or "").split()).strip()
        if not raw:
            return False
        target = cls.extract_target(raw)
        if not target:
            return False
        if raw == target or raw.strip("[]") == target:
            try:
                ipaddress.ip_address(target)
                return True
            except ValueError:
                return False
        lower = raw.lower().replace("ё", "е")
        markers = (
            "тспу", "блокиров", "заблок", "доступ", "доступен", "доступность",
            "проверь ip", "проверить ip", "проверка ip", "порт", "ssh", "rdp",
            "connection timeout", "таймаут", "чебур", "check-host",
        )
        return any(marker in lower for marker in markers)

    @staticmethod
    def _request(url: str, *, accept: str, timeout: float):
        req = request.Request(
            url,
            headers={
                "Accept": accept,
                "User-Agent": "Leshy-NetworkDiagnostics/1.0 (+https://github.com/jaster351/leshy-AI)",
            },
            method="GET",
        )
        return request.urlopen(req, timeout=timeout)

    @classmethod
    def _http_json(cls, url: str, *, timeout: float) -> Any:
        with cls._request(url, accept="application/json", timeout=timeout) as response:
            payload = response.read(2_000_000)
        return json.loads(payload.decode("utf-8", errors="replace"))

    def _local_check(self, target: str, port: int) -> TcpObservation:
        started = time.monotonic()
        try:
            with socket.create_connection((target, int(port)), timeout=self.local_timeout_seconds) as sock:
                address = ""
                try:
                    address = str(sock.getpeername()[0])
                except Exception:
                    pass
            return TcpObservation(
                source="local",
                port=int(port),
                status="connected",
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                address=address,
            )
        except (socket.timeout, TimeoutError) as exc:
            return TcpObservation(source="local", port=int(port), status="timeout", error=str(exc) or "timeout")
        except ConnectionRefusedError as exc:
            return TcpObservation(source="local", port=int(port), status="refused", error=str(exc))
        except OSError as exc:
            text = str(exc)
            status = "timeout" if "timed out" in text.lower() else "error"
            return TcpObservation(source="local", port=int(port), status=status, error=text)

    def _check_local_ports(self, target: str) -> tuple[TcpObservation, ...]:
        with ThreadPoolExecutor(max_workers=len(DEFAULT_PORTS), thread_name_prefix="leshy-block-local") as pool:
            futures = {pool.submit(self._local_check, target, port): port for port in DEFAULT_PORTS}
            results = [future.result() for future in as_completed(futures)]
        return tuple(sorted(results, key=lambda item: item.port))

    def _cheburcheck(self, target: str) -> CheburcheckEvidence:
        url = CHEBURCHECK_ENDPOINT + "?" + parse.urlencode({"target": target})
        try:
            payload = self._http_json(url, timeout=self.http_timeout_seconds)
            if not isinstance(payload, dict) or type(payload.get("blocked")) is not bool:
                raise ValueError("Cheburcheck вернул неполный или некорректный ответ")
            response_target = str(payload.get("target") or target).strip()
            if response_target.lower() != target.lower():
                raise ValueError("Cheburcheck вернул ответ для другой цели")
            providers = payload.get("cdn_providers") or {}
            whitelist = payload.get("whitelist")
            return CheburcheckEvidence(
                ok=True,
                blocked=bool(payload.get("blocked")),
                target=str(payload.get("target") or target),
                target_type=str(payload.get("target_type") or ""),
                query_id=str(payload.get("id") or ""),
                rkn_domain=str(payload.get("rkn_domain") or ""),
                blocked_subnets=tuple(str(x) for x in (payload.get("blocked_subnets") or [])[:100]),
                cdn_providers=tuple(sorted(str(x) for x in providers.keys())),
                whitelisted=bool(whitelist),
                source_url=url,
            )
        except Exception as exc:
            problem("network_blocking_cheburcheck_failed", str(exc), target=target)
            return CheburcheckEvidence(ok=False, target=target, source_url=url, error=str(exc))

    @staticmethod
    def _dispatch_sse_event(event_name: str, data_lines: list[str], state: dict[str, Any]) -> bool:
        if not data_lines:
            return False
        try:
            payload = json.loads("\n".join(data_lines))
        except Exception:
            return False
        if event_name == "started" and isinstance(payload, dict):
            state["online_probes"] = int(payload.get("online_probes") or 0)
        elif event_name == "result" and isinstance(payload, dict):
            verdicts = tuple(str(value) for value in (payload.get("verdicts") or []) if value)
            state.setdefault("observations", []).append(
                CheburProbeObservation(
                    probe_id=str(payload.get("probe_id") or ""),
                    region=str(payload.get("region") or ""),
                    provider=str(payload.get("provider") or ""),
                    asn=str(payload.get("asn") or ""),
                    verdicts=verdicts,
                    target_hop=int(payload["target_hop"]) if payload.get("target_hop") is not None else None,
                    dpi_hop=int(payload["dpi_hop"]) if payload.get("dpi_hop") is not None else None,
                )
            )
        elif event_name == "done" and isinstance(payload, dict):
            state["responded_probes"] = int(payload.get("responded_probes") or payload.get("responses") or len(state.get("observations", [])))
            if payload.get("online_probes") is not None:
                state["online_probes"] = int(payload.get("online_probes") or 0)
            return True
        return False

    def _chebur_probe(self, query_id: str) -> CheburProbeSummary:
        if not query_id:
            return CheburProbeSummary(ok=False, error="Cheburcheck не выдал id для динамической проверки")
        url = CHEBURCHECK_PROBE_ENDPOINT.format(query_id=parse.quote(query_id, safe=""))
        state: dict[str, Any] = {"online_probes": 0, "responded_probes": 0, "observations": []}
        deadline = time.monotonic() + self.chebur_probe_wait_seconds
        event_name = "message"
        data_lines: list[str] = []
        try:
            with self._request(
                url,
                accept="text/event-stream",
                timeout=self.chebur_probe_wait_seconds + 10.0,
            ) as response:
                for raw_line in response:
                    if time.monotonic() > deadline:
                        break
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        done = self._dispatch_sse_event(event_name, data_lines, state)
                        event_name = "message"
                        data_lines = []
                        if done:
                            break
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip() or "message"
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    self._dispatch_sse_event(event_name, data_lines, state)
            observations = tuple(state.get("observations") or ())
            responded = int(state.get("responded_probes") or len(observations))
            return CheburProbeSummary(
                ok=True,
                online_probes=int(state.get("online_probes") or 0),
                responded_probes=responded,
                observations=observations,
            )
        except Exception as exc:
            problem("network_blocking_chebur_probe_failed", str(exc))
            return CheburProbeSummary(
                ok=False,
                online_probes=int(state.get("online_probes") or 0),
                responded_probes=len(state.get("observations") or ()),
                observations=tuple(state.get("observations") or ()),
                error=str(exc),
            )

    def _nodes(self) -> dict[str, dict[str, Any]]:
        stamp, cached = self._nodes_cache
        if cached and time.monotonic() - stamp < 3600.0:
            return cached
        payload = self._http_json(CHECK_HOST_NODES_ENDPOINT, timeout=self.http_timeout_seconds)
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        normalized = dict(nodes or {})
        self._nodes_cache = (time.monotonic(), normalized)
        return normalized

    @staticmethod
    def _node_location(meta: Any) -> tuple[str, str, str]:
        if isinstance(meta, dict):
            location = meta.get("location") or []
        elif isinstance(meta, (list, tuple)):
            location = meta[:3]
        else:
            location = []
        code = str(location[0] if len(location) > 0 else "").lower()
        country = str(location[1] if len(location) > 1 else "")
        city = str(location[2] if len(location) > 2 else "")
        return code, country, city

    def _select_nodes(self) -> dict[str, dict[str, Any]]:
        nodes = self._nodes()
        ru: list[tuple[str, dict[str, Any]]] = []
        foreign: list[tuple[str, dict[str, Any]]] = []
        preferred = {"de": 0, "fi": 1, "nl": 2, "pl": 3, "se": 4, "us": 5}
        for name, meta in nodes.items():
            code, _country, _city = self._node_location(meta)
            if code == "ru":
                ru.append((name, meta))
            elif code:
                foreign.append((name, meta))
        ru.sort(key=lambda item: item[0])
        foreign.sort(key=lambda item: (preferred.get(self._node_location(item[1])[0], 99), item[0]))
        selected = ru[:2] + foreign[:4]
        if not selected:
            selected = list(nodes.items())[:6]
        return dict(selected)

    @staticmethod
    def _parse_check_host_result(node: str, meta: Any, port: int, value: Any) -> TcpObservation | None:
        code, country, city = NetworkBlockingChecker._node_location(meta)
        if value is None:
            return None
        row = value[0] if isinstance(value, list) and value else value
        if isinstance(row, list) and row:
            row = row[0]
        if isinstance(row, dict):
            error_text = str(row.get("error") or "")
            if error_text:
                lower = error_text.lower()
                if "timed out" in lower or "timeout" in lower:
                    status = "timeout"
                elif "refused" in lower:
                    status = "refused"
                else:
                    status = "error"
                return TcpObservation(
                    source="check-host",
                    port=port,
                    status=status,
                    country_code=code,
                    country=country,
                    city=city,
                    node=node,
                    error=error_text,
                )
            latency = row.get("time")
            address = str(row.get("address") or "")
            if latency is not None or address:
                try:
                    latency_ms = int(float(latency) * 1000) if latency is not None else None
                except (TypeError, ValueError):
                    latency_ms = None
                return TcpObservation(
                    source="check-host",
                    port=port,
                    status="connected",
                    country_code=code,
                    country=country,
                    city=city,
                    node=node,
                    latency_ms=latency_ms,
                    address=address,
                )
        text = str(row or "")
        if text:
            lower = text.lower()
            status = "timeout" if "timeout" in lower or "timed out" in lower else "refused" if "refused" in lower else "error"
            return TcpObservation(
                source="check-host",
                port=port,
                status=status,
                country_code=code,
                country=country,
                city=city,
                node=node,
                error=text[:500],
            )
        return None

    @staticmethod
    def _host_port(target: str, port: int) -> str:
        try:
            address = ipaddress.ip_address(target)
            if address.version == 6:
                return f"[{target}]:{port}"
        except ValueError:
            pass
        return f"{target}:{port}"

    def _check_host_port(self, target: str, port: int, nodes: dict[str, dict[str, Any]]) -> tuple[TcpObservation, ...]:
        query: list[tuple[str, str]] = [("host", self._host_port(target, port))]
        query.extend(("node", node) for node in nodes)
        start_url = CHECK_HOST_START_ENDPOINT + "?" + parse.urlencode(query)
        try:
            start = self._http_json(start_url, timeout=self.http_timeout_seconds)
            request_id = str(start.get("request_id") or "") if isinstance(start, dict) else ""
            if not request_id or start.get("ok") != 1:
                raise RuntimeError("Check-Host не подтвердил запуск диагностики")
            result_url = CHECK_HOST_RESULT_ENDPOINT.format(request_id=parse.quote(request_id, safe=""))
            report_url = str(start.get("permanent_link") or "")
            if not (report_url.startswith("https://check-host.net/check-report/") and len(report_url) < 240):
                report_url = result_url
            deadline = time.monotonic() + self.check_host_wait_seconds
            latest: dict[str, Any] = {}
            while time.monotonic() < deadline:
                payload = self._http_json(result_url, timeout=self.http_timeout_seconds)
                if isinstance(payload, dict):
                    latest = payload
                if latest and all(latest.get(node) is not None for node in nodes):
                    break
                time.sleep(0.8)
            observations: list[TcpObservation] = []
            for node, meta in nodes.items():
                parsed_result = self._parse_check_host_result(node, meta, int(port), latest.get(node))
                if parsed_result is None:
                    code, country, city = self._node_location(meta)
                    parsed_result = TcpObservation(
                        source="check-host",
                        port=int(port),
                        status="pending",
                        country_code=code,
                        country=country,
                        city=city,
                        node=node,
                        error="Результат не получен до истечения времени ожидания",
                    )
                observations.append(replace(parsed_result, source_url=report_url))
            return tuple(observations)
        except Exception as exc:
            problem("network_blocking_check_host_failed", str(exc), target=target, port=port)
            return tuple(
                TcpObservation(
                    source="check-host", port=int(port), status="error",
                    country_code=self._node_location(meta)[0], node=name,
                    error=f"Ошибка сервиса Check-Host: {type(exc).__name__}: {str(exc)[:180]}",
                )
                for name, meta in nodes.items()
            )

    def _check_host(self, target: str) -> tuple[TcpObservation, ...]:
        try:
            nodes = self._select_nodes()
        except Exception as exc:
            problem("network_blocking_check_host_nodes_failed", str(exc), target=target)
            return tuple(
                TcpObservation(
                    source="check-host", port=port, status="error",
                    error=f"Не удалось получить список узлов Check-Host: {type(exc).__name__}",
                )
                for port in DEFAULT_PORTS
            )
        if not nodes:
            return ()
        observations: list[TcpObservation] = []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="leshy-block-checkhost") as pool:
            futures = {pool.submit(self._check_host_port, target, port, nodes): port for port in DEFAULT_PORTS}
            for future in as_completed(futures):
                observations.extend(future.result())
        return tuple(sorted(observations, key=lambda item: (item.port, item.country_code, item.node)))

    @staticmethod
    def _service_profile(local: tuple[TcpObservation, ...], remote: tuple[TcpObservation, ...]) -> tuple[str, tuple[int, ...]]:
        open_ports: list[int] = []
        for port in DEFAULT_PORTS:
            if any(item.port == port and item.status == "connected" for item in (*local, *remote)):
                open_ports.append(port)
        if open_ports == [22]:
            return "Linux/SSH", (22,)
        if open_ports == [3389]:
            return "Windows/RDP", (3389,)
        if set(open_ports) == {22, 3389}:
            return "SSH + RDP (ОС неоднозначна)", (22, 3389)
        return "Не определён", ()

    @staticmethod
    def _assessment(
        target: str,
        local: tuple[TcpObservation, ...],
        remote: tuple[TcpObservation, ...],
        chebur: CheburcheckEvidence,
        probe: CheburProbeSummary | None = None,
    ) -> BlockingAssessment:
        probe = probe or CheburProbeSummary(ok=False, error="Динамический probe не выполнялся")
        profile, service_ports = NetworkBlockingChecker._service_profile(local, remote)
        reasons: list[str] = []
        score = 0

        official_tspu = any("tspu_block" in item.verdicts for item in probe.observations)
        official_ok = bool(probe.observations) and all(
            item.verdicts and all(verdict in {"ok", "uncertain"} for verdict in item.verdicts)
            for item in probe.observations
        ) and any("ok" in item.verdicts for item in probe.observations)

        ports_to_assess = service_ports or DEFAULT_PORTS
        local_timeouts = any(item.port in ports_to_assess and item.status == "timeout" for item in local)
        completed_remote = tuple(
            item for item in remote
            if item.port in ports_to_assess and item.status in {"connected", "timeout", "refused"}
        )
        successful_observations = sum(
            item.status == "connected" for item in (*local, *completed_remote)
            if item.port in ports_to_assess and item.status in {"connected", "timeout", "refused"}
        )
        total_observations = sum(
            item.port in ports_to_assess and item.status in {"connected", "timeout", "refused"}
            for item in (*local, *completed_remote)
        )
        geographic_tspu = False
        foreign_success_any = False
        ru_success_any = False
        for port in ports_to_assess:
            ru = [item for item in completed_remote if item.port == port and item.country_code == "ru"]
            foreign = [
                item for item in completed_remote
                if item.port == port and item.country_code and item.country_code != "ru"
            ]
            ru_timeout = any(item.status == "timeout" for item in ru)
            ru_connected = any(item.status == "connected" for item in ru)
            foreign_connected = any(item.status == "connected" for item in foreign)
            foreign_success_any = foreign_success_any or foreign_connected
            ru_success_any = ru_success_any or ru_connected
            if ru_timeout and foreign_connected and not ru_connected:
                geographic_tspu = True
                reasons.append(
                    f"TCP/{port}: узел Check-Host в РФ получает timeout, а зарубежный подключается. "
                    "Это признак сетевой фильтрации, не доказательство механизма ТСПУ."
                )
            if ru_timeout and foreign_connected and ru_connected:
                reasons.append(
                    f"TCP/{port}: ответы российских узлов противоречивы; некоторые узлы подключаются."
                )

        # Diagnostic index, not a calibrated probability. External services and
        # multiple independent vantage points are required for a high score.
        score = 0
        if local_timeouts:
            score += 10
            reasons.append("Локальный TCP Connect завершился тайм-аутом.")
        if chebur.ok and chebur.blocked:
            score += 25
            reasons.append("Cheburcheck сообщил о наличии цели в проверяемых списках.")
        elif chebur.ok and chebur.blocked is False:
            reasons.append("Cheburcheck не нашёл цель в проверяемых списках.")
        elif not chebur.ok:
            reasons.append("Чебурчек не предоставил достоверный ответ.")

        if official_tspu:
            score += 45
            reasons.append(
                "Распределённый Cheburcheck Probe сообщил tspu_block. "
                "Вердикт сервиса — сильный признак, но не прямое доказательство ТСПУ."
            )
        elif official_ok:
            reasons.append("Динамический Cheburcheck Probe сообщил о доступности.")

        if geographic_tspu:
            score += 35
            reasons.append(
                "Различие TCP-доступности из России и из-за рубежа подтверждено "
                "измерениями Check-Host."
            )
        elif foreign_success_any and ru_success_any:
            reasons.append("Есть успешные подключения и из России, и из-за рубежа.")

        if geographic_tspu and (official_tspu or (chebur.ok and chebur.blocked)):
            score += 10
            reasons.append(
                "Географический признак Check-Host согласуется с независимыми данными Чебурчека."
            )

        if official_ok and not official_tspu and not geographic_tspu:
            score = min(score, 20)
        if foreign_success_any and ru_success_any and not official_tspu and not geographic_tspu:
            score = min(score, 20)
        if chebur.ok and chebur.whitelisted and not official_tspu:
            score = min(score, 30)
            reasons.append("Cheburcheck сообщает об исключении/белом списке.")

        if completed_remote and all(item.status == "refused" for item in completed_remote):
            reasons.append(
                "Все завершённые ответы Check-Host — Connection Refused: "
                "это не признак региональной ТСПУ-фильтрации само по себе."
            )
            if not official_tspu:
                score = min(score, 35 if chebur.ok and chebur.blocked else 10)

        if not completed_remote:
            reasons.append("Check-Host не предоставил завершённых TCP-измерений.")
        if not probe.observations:
            reasons.append("Нет завершённых измерений распределённого Cheburcheck Probe.")
        availability = int(round(successful_observations * 100 / total_observations)) if total_observations else 0
        if score >= 70:
            verdict = "Есть серьёзные признаки фильтрации, но блокировка ТСПУ не подтверждена"
        elif score >= 30:
            verdict = "Есть косвенные признаки фильтрации, но блокировка ТСПУ не подтверждена"
        else:
            verdict = "Блокировка ТСПУ не подтверждена"

        if not service_ports:
            reasons.append("Ни TCP/22, ни TCP/3389 не подтвердили работающий сервис; ОС не угадывается по закрытым портам.")
        if probe.ok and probe.online_probes and not probe.observations:
            reasons.append("Cheburcheck сообщил о доступных probe-узлах, но ни один результат не успел вернуться.")

        return BlockingAssessment(
            target=target,
            profile=profile,
            service_ports=service_ports,
            tspu_probability=max(0, min(99, int(score))),
            verdict=verdict,
            availability_percent=max(0, min(100, availability)),
            local=local,
            remote=remote,
            cheburcheck=chebur,
            chebur_probe=probe,
            reasons=tuple(dict.fromkeys(reasons)),
            checked_at_epoch=time.time(),
        )

    def check(self, target: str) -> BlockingAssessment:
        normalized = self.normalize_target(target)
        event("network_blocking_check_started", target=normalized, ports=list(DEFAULT_PORTS))

        # 1. Сначала фактический TCP из собственной сети.
        local = self._check_local_ports(normalized)
        # 2. Затем статический Cheburcheck и получение подписанного query id.
        chebur = self._cheburcheck(normalized)
        # 3. После этого официальный dynamic probe + независимый Check-Host.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="leshy-blocking-remote") as pool:
            remote_future = pool.submit(self._check_host, normalized)
            probe_future = pool.submit(self._chebur_probe, chebur.query_id)
            remote = remote_future.result()
            probe = probe_future.result()

        result = self._assessment(normalized, local, remote, chebur, probe)
        receipts = [
            self._receipts.record("tcp.connect", "success" if local else "error", [asdict(x) for x in local]),
            self._receipts.record("cheburcheck.check", "success" if chebur.ok else "error", asdict(chebur)),
            self._receipts.record(
                "check_host.tcp",
                "success" if any(x.status in {"connected", "refused", "timeout"} for x in remote) else "error",
                [asdict(x) for x in remote],
            ),
        ]
        if chebur.query_id:
            receipts.append(self._receipts.record(
                "cheburcheck.probe",
                "success" if probe.ok and probe.observations else "error",
                asdict(probe),
            ))
        result = replace(result, tool_receipts=tuple(receipts))
        event(
            "network_blocking_check_finished",
            target=normalized,
            profile=result.profile,
            tspu_probability=result.tspu_probability,
            availability_percent=result.availability_percent,
            chebur_blocked=result.cheburcheck.blocked,
            chebur_probe_results=result.chebur_probe.responded_probes,
            chebur_probe_tspu=any("tspu_block" in item.verdicts for item in result.chebur_probe.observations),
        )
        return result

    @staticmethod
    def format(result: BlockingAssessment) -> str:
        local_by_port = {item.port: item for item in result.local}
        checked = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(result.checked_at_epoch))
        lines = [
            f"Проверка {result.target}",
            f"Время проверки: {checked}",
            f"Оценка уверенности в признаках блокировки ТСПУ: {result.tspu_probability}% (условный диагностический индекс, не статистическая вероятность).",
            "Статус подтверждения: блокировка ТСПУ не подтверждена.",
            f"Профиль сервера: {result.profile}",
        ]
        for port, label in ((22, "SSH/Linux"), (3389, "RDP/Windows")):
            item = local_by_port.get(port)
            if item is None:
                lines.append(f"Локально TCP/{port} ({label}): нет результата")
            elif item.status == "connected":
                latency = f" за {item.latency_ms} мс" if item.latency_ms is not None else ""
                lines.append(f"Локально TCP/{port} ({label}): доступен{latency}")
            else:
                lines.append(f"Локально TCP/{port} ({label}): {item.status}" + (f" ({item.error[:180]})" if item.error else ""))

        chebur = result.cheburcheck
        if chebur.ok:
            state = "найден в блокировочных списках" if chebur.blocked else "не найден в блокировочных списках"
            extra: list[str] = []
            if chebur.rkn_domain:
                extra.append(f"домен РКН: {chebur.rkn_domain}")
            if chebur.blocked_subnets:
                extra.append("подсети: " + ", ".join(chebur.blocked_subnets[:4]))
            if chebur.cdn_providers:
                extra.append("CDN: " + ", ".join(chebur.cdn_providers[:4]))
            if chebur.whitelisted:
                extra.append("есть исключение/белый список")
            lines.append("Cheburcheck: " + state + (("; " + "; ".join(extra)) if extra else ""))
        else:
            lines.append("Cheburcheck: проверить не удалось" + (f" ({chebur.error})" if chebur.error else ""))

        probe = result.chebur_probe
        if probe.ok:
            if probe.observations:
                parts: list[str] = []
                for item in probe.observations:
                    location = item.region or item.provider or ("probe " + item.probe_id)
                    verdicts = ",".join(item.verdicts) or "uncertain"
                    parts.append(f"{location}: {verdicts}")
                lines.append(
                    f"Cheburcheck Probe ({probe.responded_probes}/{probe.online_probes or probe.responded_probes}): "
                    + "; ".join(parts)
                )
            else:
                lines.append(f"Cheburcheck Probe: результатов нет ({probe.responded_probes}/{probe.online_probes})")
        elif probe.error:
            lines.append("Cheburcheck Probe: " + probe.error)
        else:
            lines.append("Cheburcheck Probe: результат отсутствует")

        for port in DEFAULT_PORTS:
            observations = [item for item in result.remote if item.port == port]
            if not observations:
                continue
            parts = []
            for item in observations:
                location = "/".join(x for x in (item.country_code.upper(), item.city) if x) or item.node
                parts.append(f"{location}: {item.status}" + (f" ({item.error[:180]})" if item.error else ""))
            lines.append(f"Check-Host TCP/{port}: " + "; ".join(parts))

        lines.append(f"Вывод по ТСПУ: {result.verdict}.")
        lines.append("Расчётный балл признаков не является процентом достоверности блокировки.")
        if not result.remote:
            lines.append("Check-Host: результатов нет; проверить российские и зарубежные узлы не удалось.")
        lines.append(f"Успешные TCP-подключения среди завершённых наблюдений: {result.availability_percent}% (не вероятность блокировки).")
        if result.reasons:
            lines.append("Почему: " + " ".join(result.reasons))
        if chebur.ok and chebur.source_url:
            lines.append("Источник Cheburcheck: " + chebur.source_url)
        report_links = sorted({item.source_url for item in result.remote if item.source_url and item.status not in {"pending", "error"}})
        for link in report_links:
            lines.append("Отчёт Check-Host: " + link)
        if result.tool_receipts:
            lines.append("Запуски инструментов (ID; статус; время UTC):")
            for receipt in result.tool_receipts:
                lines.append(f"{receipt.tool}: {receipt.receipt_id}; {receipt.status}; {receipt.timestamp_utc}")
        return "\n".join(lines)


@dataclass(slots=True)
class NetworkBlockingModule:
    checker: NetworkBlockingChecker = field(default_factory=NetworkBlockingChecker)
    manifest: ModuleManifest = field(
        default_factory=lambda: ModuleManifest(
            name="network-blocking",
            version="0.2.0",
            description="TCP/22+3389, Cheburcheck static+distributed Probe и Check-Host для диагностики блокировок ТСПУ",
            priority=850,
        )
    )

    def can_handle(self, request: ModuleRequest) -> bool:
        event_name = str(request.metadata.get("event") or "").strip()
        return event_name == "network_blocking_check" or self.checker.looks_like_request(request.text)

    def handle(self, request: ModuleRequest) -> ModuleResponse:
        target = str(request.metadata.get("target") or "").strip() or self.checker.extract_target(request.text)
        if not target:
            return ModuleResponse(handled=False)
        result = self.checker.check(target)
        return ModuleResponse(
            handled=True,
            text=self.checker.format(result),
            metadata={
                "module": self.manifest.name,
                "target": result.target,
                "profile": result.profile,
                "service_ports": list(result.service_ports),
                "tspu_probability": result.tspu_probability,
                "diagnostic_score": result.tspu_probability,
                "diagnostic_score_type": "heuristic_not_calibrated_probability",
                "blocking_attribution": "unconfirmed",
                "copyable_conclusion": "",
                "availability_percent": result.availability_percent,
                "verdict": result.verdict,
                "local": [asdict(item) for item in result.local],
                "remote": [asdict(item) for item in result.remote],
                "cheburcheck": asdict(result.cheburcheck),
                "chebur_probe": asdict(result.chebur_probe),
                "reasons": list(result.reasons),
                "checked_at_epoch": result.checked_at_epoch,
                "tool_receipts": [asdict(r) for r in result.tool_receipts],
            },
        )


def create_network_blocking_module() -> NetworkBlockingModule:
    return NetworkBlockingModule()


__all__ = [
    "BlockingAssessment",
    "CheburProbeObservation",
    "CheburProbeSummary",
    "CheburcheckEvidence",
    "NetworkBlockingChecker",
    "NetworkBlockingModule",
    "TcpObservation",
    "create_network_blocking_module",
]
