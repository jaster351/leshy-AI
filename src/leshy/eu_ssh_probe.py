"""Independent RU-vs-Netherlands TCP/22 and TCP/3389 diagnostic using private probe.

A successful TCP connection checks service reachability, not credentials.
No SSH login is attempted. Country claims are operator configuration, not IP geolocation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
from urllib import error, parse, request

EUROPE_COUNTRIES = frozenset((
    "AL", "AD", "AT", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI",
    "FR", "DE", "GR", "HU", "IS", "IE", "IT", "XK", "LV", "LI", "LT", "LU",
    "MT", "MD", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "SM", "RS",
    "SK", "SI", "ES", "SE", "CH", "UA", "GB", "VA",
))


def configured() -> bool:
    return bool(
        os.environ.get("LESHY_EU_PROBE_URL", "").strip()
        and os.environ.get("LESHY_EU_PROBE_PASSWORD", "").strip()
    )


def _tcp_port(ip: str, port: int, timeout: float = 3.0) -> dict:
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            status = "connected"
    except ConnectionRefusedError:
        status = "refused"
    except (TimeoutError, socket.timeout):
        status = "timeout"
    except OSError:
        status = "error"
    return {
        "status": status,
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _tcp22(ip: str, timeout: float = 3.0) -> dict:
    return _tcp_port(ip, 22, timeout)


def _tcp3389(ip: str, timeout: float = 3.0) -> dict:
    return _tcp_port(ip, 3389, timeout)


class _NeverRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _remote_port(ip: str, port: int, *, timeout: float) -> dict:
    if port not in (22, 3389):
        raise ValueError("Разрешены только TCP/22 и TCP/3389")
    base = os.environ["LESHY_EU_PROBE_URL"].strip().rstrip("/")
    password = os.environ["LESHY_EU_PROBE_PASSWORD"].strip()
    if len(password) < 48 or len(password) > 128 or not password.isascii() or not password.isdecimal():
        raise ValueError("LESHY_EU_PROBE_PASSWORD должен содержать 48-128 цифр")
    url = parse.urlsplit(base)
    if (
        url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
        or url.query or url.fragment or (url.path not in ("", "/"))
    ):
        raise ValueError("LESHY_EU_PROBE_URL должен быть HTTP(S)-адресом без пути и параметров")
    nonce = secrets.token_hex(16)
    timestamp = int(time.time())
    message = f"v1|{ip}|{nonce}|{timestamp}|{port}".encode("ascii")
    proof = hmac.new(password.encode("ascii"), message, hashlib.sha256).hexdigest()
    payload = json.dumps({
        "target": ip, "nonce": nonce, "timestamp": timestamp, "password": proof, "port": port,
    }, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        base + ("/v1/ssh/check" if port == 22 else "/v1/rdp/check"), data=payload, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", "Cache-Control": "no-store"},
    )
    opener = request.build_opener(_NeverRedirect)
    with opener.open(req, timeout=timeout) as res:
        if res.status != 200:
            raise ValueError("EU probe: неожиданный HTTP-статус")
        data = res.read(16385)
    if len(data) > 16384:
        raise ValueError("EU probe: слишком большой ответ")
    result = json.loads(data.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("EU probe: некорректный JSON")
    signature = result.pop("signature", "")
    canonical = json.dumps(
        result, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected_signature = hmac.new(
        password.encode("ascii"), canonical, hashlib.sha256
    ).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected_signature):
        raise ValueError("EU probe: подпись ответа не совпала")
    if (
        result.get("target") != ip or result.get("port") != port
        or result.get("nonce") != nonce or result.get("completed") is not True
    ):
        raise ValueError("EU probe: ответ не совпадает с запросом")
    country = str(result.get("country_code") or "").upper()
    if country not in EUROPE_COUNTRIES:
        raise ValueError("EU probe: регион зарубежного узла не подтверждён настройками")
    # The signed node response is authoritative for the *configured* country.
    # A stale Windows preference (e.g. DE after migrating the node to NL) must
    # not make both port probes fail; still return the actual signed country.
    expected = os.environ.get("LESHY_EU_PROBE_EXPECTED_COUNTRY", "").strip().upper()
    country_setting_mismatch = bool(expected and country != expected)
    try:
        stamp = datetime.fromisoformat(str(result["checked_at_utc"]).replace("Z", "+00:00"))
        if abs((datetime.now(timezone.utc) - stamp).total_seconds()) > 45:
            raise ValueError("EU probe: время результата устарело")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("EU probe: некорректное время результата") from exc
    attempts = result.get("attempts")
    if (
        not isinstance(attempts, list) or len(attempts) != 3
        or any(
            not isinstance(item, dict)
            or item.get("status") not in ("connected", "timeout", "refused", "error")
            for item in attempts
        )
    ):
        raise ValueError("EU probe: неполные результаты трёх TCP-попыток")
    return {
        "country_code": country,
        "country_setting_mismatch": country_setting_mismatch,
        "node": str(result.get("node") or "")[:80],
        "attempts": attempts,
        "checked_at_utc": str(result["checked_at_utc"]),
    }


def _remote_ssh22(ip: str, *, timeout: float) -> dict:
    return _remote_port(ip, 22, timeout=timeout)


def _remote_rdp3389(ip: str, *, timeout: float) -> dict:
    return _remote_port(ip, 3389, timeout=timeout)


def compare_ssh22(ip: str) -> dict:
    """Compare three local TCP handshakes with three remote handshakes.

    The local vantage must be explicitly confirmed as a Russian connection with
    no proxy/VPN, otherwise geographic attribution is withheld.
    """
    try:
        target = ipaddress.ip_address(str(ip).strip())
        if not target.is_global or getattr(target, "ipv4_mapped", None):
            raise ValueError("IP должен быть публичным")
    except ValueError:
        return {"status": "invalid_target", "reason": "нужен публичный IP-адрес"}
    ip = str(target)
    if not configured():
        return {"status": "not_configured", "target": ip, "port": 22}

    timeout = 2.5
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="leshy-eu-ssh") as pool:
        remote = pool.submit(_remote_ssh22, ip, timeout=14.0)
        local_results = [_tcp22(ip, timeout) for _ in range(3)]
        try:
            eu = remote.result()
        except (error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
            return {
                "status": "probe_error", "target": ip, "port": 22,
                "local_attempts": local_results,
                "reason": f"зарубежный агент недоступен: {type(exc).__name__}",
            }

    result = {
        "target": ip, "port": 22, "status": "inconclusive",
        "local_attempts": local_results,
        "eu_attempts": eu["attempts"],
        "eu_country_code": eu["country_code"], "eu_node": eu["node"],
        "eu_checked_at_utc": eu["checked_at_utc"],
    }
    # An operator must declare that their local connection actually exits via RU.
    if os.environ.get("LESHY_RU_PROBE_CONFIRMED", "").strip() != "1":
        result["status"] = "local_region_unverified"
        result["reason"] = "Российский выход Лешего не подтверждён; проверьте VPN/прокси"
        return result

    local = [x["status"] for x in local_results]
    abroad = [x["status"] for x in eu["attempts"]]
    if all(x == "connected" for x in local) and all(x == "connected" for x in abroad):
        result["status"] = "both_connected"
        result["reason"] = "TCP/22 доступен из России и с зарубежного узла"
    elif all(x == "timeout" for x in local) and all(x == "connected" for x in abroad):
        result["status"] = "ru_unreachable_eu_reachable"
        result["reason"] = "Три тайм-аута из РФ при трёх успешных соединениях зарубежного узла"
    elif all(x in ("timeout", "refused") for x in local + abroad):
        result["status"] = "both_unreachable"
        result["reason"] = "Порт SSH недоступен с обеих сторон; источник ограничения не установлен"
    elif all(x == "connected" for x in local):
        result["status"] = "ru_connected"
        result["reason"] = "TCP/22 доступен из РФ; блокировка этого порта из РФ не выявлена"
    else:
        result["reason"] = "Ответы нестабильны либо противоречивы; региональная блокировка не установлена"
    return result


def compare_rdp3389(ip: str) -> dict:
    """Independent three-attempt RDP/3389 comparison, same safeguards as SSH."""
    try:
        target = ipaddress.ip_address(str(ip).strip())
        if not target.is_global or getattr(target, "ipv4_mapped", None):
            raise ValueError("IP must be public")
    except ValueError:
        return {"status": "invalid_target", "port": 3389}
    ip = str(target)
    if not configured():
        return {"status": "not_configured", "target": ip, "port": 3389}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="leshy-eu-rdp") as pool:
        remote = pool.submit(_remote_rdp3389, ip, timeout=14.0)
        local_results = [_tcp3389(ip, 2.5) for _ in range(3)]
        try:
            eu = remote.result()
        except (error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
            return {
                "status": "probe_error", "target": ip, "port": 3389,
                "local_attempts": local_results,
                "reason": f"Зарубежный агент TCP/3389 недоступен: {type(exc).__name__}",
            }
    result = {
        "target": ip, "port": 3389, "status": "inconclusive",
        "local_attempts": local_results, "eu_attempts": eu["attempts"],
        "eu_country_code": eu["country_code"], "eu_node": eu["node"],
        "eu_checked_at_utc": eu["checked_at_utc"],
    }
    local = [x["status"] for x in local_results]
    abroad = [x["status"] for x in eu["attempts"]]
    if all(x == "connected" for x in local) and all(x == "connected" for x in abroad):
        result["status"] = "both_connected"
        result["reason"] = "RDP/3389 доступен из обеих сетей"
    elif all(x == "connected" for x in local):
        result["status"] = "ru_connected"
        result["reason"] = "RDP/3389 доступен из сети Лешего"
    elif os.environ.get("LESHY_RU_PROBE_CONFIRMED", "").strip() != "1":
        result["status"] = "local_region_unverified"
        result["reason"] = "Нельзя подтвердить, что сеть Лешего находится в России без VPN"
    elif all(x == "timeout" for x in local) and all(x == "connected" for x in abroad):
        result["status"] = "ru_unreachable_eu_reachable"
        result["reason"] = "Три тайм-аута RDP из РФ, три успешных соединения из Европы"
    elif all(x in ("timeout", "refused") for x in local + abroad):
        result["status"] = "both_unreachable"
        result["reason"] = "RDP/3389 не доступен из обеих сетей; причина неизвестна"
    else:
        result["reason"] = "RDP/3389: смешанные результаты, причина не установлена"
    return result


def compare_tcp_ports(ip: str) -> dict:
    """Both independent ports; preserve existing SSH fields for older consumers."""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="leshy-eu-both") as pool:
        ssh = pool.submit(compare_ssh22, ip)
        rdp = pool.submit(compare_rdp3389, ip)
        ssh_result = ssh.result()
        rdp_result = rdp.result()
    return {
        **ssh_result,
        "ports": {"22": ssh_result, "3389": rdp_result},
    }
