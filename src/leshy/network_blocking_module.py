from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
import ipaddress
import json
import re
import socket
import time
from typing import Any
from urllib import parse, request

from .diagnostics import event, problem
from .modules import ModuleManifest, ModuleRequest, ModuleResponse


DEFAULT_PORTS = (22, 3389)
CHEBURCHECK_ENDPOINT = "https://cheburcheck.ru/api/v1/check"
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


@dataclass(frozen=True, slots=True)
class CheburcheckEvidence:
    ok: bool
    blocked: bool | None = None
    target: str = ""
    target_type: str = ""
    rkn_domain: str = ""
    blocked_subnets: tuple[str, ...] = ()
    cdn_providers: tuple[str, ...] = ()
    whitelisted: bool = False
    source_url: str = ""
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
    reasons: tuple[str, ...]
    checked_at_epoch: float


class NetworkBlockingChecker:
    """Диагностика блокировок без изменения удалённого сервера.

    Методика объединяет три независимых класса признаков:
    1. TCP connect из сети самого Лешего одновременно на SSH/22 и RDP/3389.
    2. Cheburcheck `/api/v1/check`: реестры РКН, заблокированные подсети и CDN.
    3. Check-Host TCP из российских и зарубежных точек.

    Географический timeout не объявляется математическим доказательством ТСПУ.
    Значение 100% означает «100 по согласованной диагностической эвристике Лешего»:
    рабочий порт отвечает хотя бы из одной зарубежной точки, а российская точка
    получает connection timeout/сетевую ошибку при той же проверке.
    """

    def __init__(
        self,
        *,
        local_timeout_seconds: float = 5.0,
        http_timeout_seconds: float = 12.0,
        check_host_wait_seconds: float = 12.0,
    ) -> None:
        self.local_timeout_seconds = max(0.5, min(float(local_timeout_seconds), 30.0))
        self.http_timeout_seconds = max(3.0, min(float(http_timeout_seconds), 30.0))
        self.check_host_wait_seconds = max(3.0, min(float(check_host_wait_seconds), 30.0))
        self._nodes_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})

    @staticmethod
    def normalize_target(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("IP-адрес или домен не указан")
        if "://" in raw:
            raw = parse.urlparse(raw).hostname or raw
        raw = raw.strip().strip("[]").rstrip(".")
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
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
    def _http_json(url: str, *, timeout: float) -> Any:
        req = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Leshy-NetworkDiagnostics/1.0 (+https://github.com/jaster351/leshy-AI)",
            },
            method="GET",
        )
        with request.urlopen(req, timeout=timeout) as response:
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
        except socket.timeout as exc:
            return TcpObservation(source="local", port=int(port), status="timeout", error=str(exc) or "timeout")
        except TimeoutError as exc:
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
            providers = payload.get("cdn_providers") or {}
            whitelist = payload.get("whitelist")
            return CheburcheckEvidence(
                ok=True,
                blocked=bool(payload.get("blocked")),
                target=str(payload.get("target") or target),
                target_type=str(payload.get("target_type") or ""),
                rkn_domain=str(payload.get("rkn_domain") or ""),
                blocked_subnets=tuple(str(x) for x in (payload.get("blocked_subnets") or [])[:100]),
                cdn_providers=tuple(sorted(str(x) for x in providers.keys())),
                whitelisted=bool(whitelist),
                source_url=url,
            )
        except Exception as exc:
            problem("network_blocking_cheburcheck_failed", str(exc), target=target)
            return CheburcheckEvidence(ok=False, target=target, source_url=url, error=str(exc))

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
        row = value
        if isinstance(value, list) and value:
            row = value[0]
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
            status = "timeout" if "timeout" in lower or "timed out" in lower else "error"
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

    def _check_host_port(self, target: str, port: int, nodes: dict[str, dict[str, Any]]) -> tuple[TcpObservation, ...]:
        query: list[tuple[str, str]] = [("host", f"{target}:{port}")]
        query.extend(("node", node) for node in nodes)
        start_url = CHECK_HOST_START_ENDPOINT + "?" + parse.urlencode(query)
        try:
            start = self._http_json(start_url, timeout=self.http_timeout_seconds)
            request_id = str(start.get("request_id") or "") if isinstance(start, dict) else ""
            if not request_id:
                raise RuntimeError("Check-Host не вернул request_id")
            result_url = CHECK_HOST_RESULT_ENDPOINT.format(request_id=parse.quote(request_id, safe=""))
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
                observations.append(parsed_result)
            return tuple(observations)
        except Exception as exc:
            problem("network_blocking_check_host_failed", str(exc), target=target, port=port)
            return ()

    def _check_host(self, target: str) -> tuple[TcpObservation, ...]:
        try:
            nodes = self._select_nodes()
        except Exception as exc:
            problem("network_blocking_check_host_nodes_failed", str(exc), target=target)
            return ()
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
    ) -> BlockingAssessment:
        profile, service_ports = NetworkBlockingChecker._service_profile(local, remote)
        reasons: list[str] = []
        score = 0

        ports_to_assess = service_ports or DEFAULT_PORTS
        successful_observations = 0
        total_observations = 0
        geographic_tspu = False
        all_relevant_remote_success = True
        any_remote_data = False

        for port in ports_to_assess:
            port_remote = [item for item in remote if item.port == port and item.status != "pending"]
            ru = [item for item in port_remote if item.country_code == "ru"]
            foreign = [item for item in port_remote if item.country_code and item.country_code != "ru"]
            ru_fail = any(item.status in {"timeout", "error"} for item in ru)
            foreign_success = any(item.status == "connected" for item in foreign)
            if ru_fail and foreign_success:
                geographic_tspu = True
                reasons.append(
                    f"TCP/{port}: российская точка даёт timeout/сетевую ошибку, а зарубежная точка подключается."
                )

            if port_remote:
                any_remote_data = True
                if not all(item.status == "connected" for item in port_remote):
                    all_relevant_remote_success = False
            else:
                all_relevant_remote_success = False

            for item in port_remote:
                total_observations += 1
                if item.status == "connected":
                    successful_observations += 1

        for item in local:
            if item.port in ports_to_assess:
                total_observations += 1
                if item.status == "connected":
                    successful_observations += 1

        if geographic_tspu:
            score = 100
            reasons.append("По согласованной эвристике Лешего это максимальный признак фильтрации по российской сети/ТСПУ.")
        elif any_remote_data and all_relevant_remote_success:
            score = 0
            reasons.append("Рабочие порты доступны из всех завершивших проверку точек Check-Host.")
        elif chebur.ok and chebur.blocked:
            score = max(score, 70)
            reasons.append("Cheburcheck нашёл адрес/подсеть в блокировочных или CDN-списках.")
        elif chebur.ok and chebur.blocked is False:
            reasons.append("Cheburcheck не обнаружил адрес в используемых блокировочных списках.")

        local_success = any(item.status == "connected" and item.port in ports_to_assess for item in local)
        foreign_success_any = any(
            item.status == "connected" and item.country_code and item.country_code != "ru" and item.port in ports_to_assess
            for item in remote
        )
        ru_success_any = any(
            item.status == "connected" and item.country_code == "ru" and item.port in ports_to_assess
            for item in remote
        )
        if not geographic_tspu and foreign_success_any and ru_success_any:
            score = min(score, 20)
            reasons.append("Рабочий порт отвечает и из России, и из зарубежных точек — географического признака ТСПУ нет.")
        if not geographic_tspu and not local_success and foreign_success_any and ru_success_any:
            score = max(score, 25)
            reasons.append("Локально соединение не удалось, но внешние российские и зарубежные точки отвечают: вероятнее локальная сеть/маршрут/фаервол.")
        if not geographic_tspu and not foreign_success_any and not ru_success_any and remote:
            score = min(score if score else 15, 70)
            reasons.append("Порт не отвечает одновременно из России и из других стран; это не характерный признак выборочной ТСПУ-фильтрации.")

        if chebur.ok and chebur.whitelisted and score < 100:
            score = min(score, 30)
            reasons.append("Cheburcheck сообщает об исключении/белом списке для цели.")

        availability = int(round(successful_observations * 100 / total_observations)) if total_observations else 0
        if score >= 100:
            verdict = "Высокая вероятность блокировки средствами ТСПУ"
        elif score >= 70:
            verdict = "Вероятна блокировка или попадание в блокировочные диапазоны"
        elif score >= 30:
            verdict = "Есть косвенные признаки, требуется повторная проверка"
        else:
            verdict = "Признаки блокировки ТСПУ не подтверждены"

        if not service_ports:
            reasons.append("Ни TCP/22, ни TCP/3389 не подтвердили работающий сервис; ОС не угадывается по закрытым портам.")

        return BlockingAssessment(
            target=target,
            profile=profile,
            service_ports=service_ports,
            tspu_probability=max(0, min(100, int(score))),
            verdict=verdict,
            availability_percent=max(0, min(100, availability)),
            local=local,
            remote=remote,
            cheburcheck=chebur,
            reasons=tuple(dict.fromkeys(reasons)),
            checked_at_epoch=time.time(),
        )

    def check(self, target: str) -> BlockingAssessment:
        normalized = self.normalize_target(target)
        event("network_blocking_check_started", target=normalized, ports=list(DEFAULT_PORTS))
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="leshy-blocking") as pool:
            local_future = pool.submit(self._check_local_ports, normalized)
            chebur_future = pool.submit(self._cheburcheck, normalized)
            remote_future = pool.submit(self._check_host, normalized)
            local = local_future.result()
            chebur = chebur_future.result()
            remote = remote_future.result()
        result = self._assessment(normalized, local, remote, chebur)
        event(
            "network_blocking_check_finished",
            target=normalized,
            profile=result.profile,
            tspu_probability=result.tspu_probability,
            availability_percent=result.availability_percent,
            chebur_blocked=result.cheburcheck.blocked,
        )
        return result

    @staticmethod
    def format(result: BlockingAssessment) -> str:
        local_by_port = {item.port: item for item in result.local}
        lines = [
            f"Проверка {result.target}",
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
                lines.append(f"Локально TCP/{port} ({label}): {item.status}")

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

        for port in DEFAULT_PORTS:
            observations = [item for item in result.remote if item.port == port]
            if not observations:
                continue
            parts = []
            for item in observations:
                location = "/".join(x for x in (item.country_code.upper(), item.city) if x) or item.node
                parts.append(f"{location}: {item.status}")
            lines.append(f"Check-Host TCP/{port}: " + "; ".join(parts))

        lines.append(f"Оценка ТСПУ: {result.tspu_probability}% — {result.verdict}.")
        lines.append(f"Доступность по завершённым TCP-наблюдениям: {result.availability_percent}%.")
        if result.reasons:
            lines.append("Почему: " + " ".join(result.reasons))
        if chebur.source_url:
            lines.append("Источник Cheburcheck: " + chebur.source_url)
        return "\n".join(lines)


@dataclass(slots=True)
class NetworkBlockingModule:
    checker: NetworkBlockingChecker = field(default_factory=NetworkBlockingChecker)
    manifest: ModuleManifest = field(
        default_factory=lambda: ModuleManifest(
            name="network-blocking",
            version="0.1.0",
            description="Проверяет TCP/22 и TCP/3389, Cheburcheck и географическую доступность Check-Host для оценки блокировок ТСПУ",
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
                "availability_percent": result.availability_percent,
                "verdict": result.verdict,
                "local": [asdict(item) for item in result.local],
                "remote": [asdict(item) for item in result.remote],
                "cheburcheck": asdict(result.cheburcheck),
                "reasons": list(result.reasons),
            },
        )


def create_network_blocking_module() -> NetworkBlockingModule:
    return NetworkBlockingModule()


__all__ = [
    "BlockingAssessment",
    "CheburcheckEvidence",
    "NetworkBlockingChecker",
    "NetworkBlockingModule",
    "TcpObservation",
    "create_network_blocking_module",
]
