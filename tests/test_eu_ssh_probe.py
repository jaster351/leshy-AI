"""No live network is used: controlled RU and EU SSH/22 outcome regression tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from leshy.eu_ssh_probe import compare_ssh22
from leshy.network_blocking_module import (
    CheburcheckEvidence, CheburProbeSummary, NetworkBlockingChecker, NetworkBlockingModule, TcpObservation,
)
from leshy.modules import ModuleRequest


CONFIG = {
    "LESHY_EU_PROBE_URL": "https://probe.example.com",
    "LESHY_EU_PROBE_TOKEN": "x" * 64,
    "LESHY_EU_PROBE_EXPECTED_COUNTRY": "DE",
    "LESHY_RU_PROBE_CONFIRMED": "1",
}


def eu(*states):
    return {
        "country_code": "DE", "node": "europe-1",
        "checked_at_utc": "2026-11-20T12:00:00Z",
        "attempts": [{"status": state, "elapsed_ms": 5} for state in states],
    }


def local(*states):
    iterator = iter(states)
    return lambda *args, **kwargs: {"status": next(iterator), "elapsed_ms": 5}


@pytest.mark.parametrize("local_states,eu_states,expected", [
    (("connected",) * 3, ("connected",) * 3, "both_connected"),
    (("timeout",) * 3, ("connected",) * 3, "ru_unreachable_eu_reachable"),
    (("timeout",) * 3, ("timeout",) * 3, "both_unreachable"),
    (("connected",) * 3, ("timeout",) * 3, "ru_connected"),
    (("timeout", "connected", "timeout"), ("connected",) * 3, "inconclusive"),
])
def test_operator_owned_eu_comparison(local_states, eu_states, expected):
    with patch.dict("os.environ", CONFIG):
        with (
            patch("leshy.eu_ssh_probe._tcp22", side_effect=local(*local_states)),
            patch("leshy.eu_ssh_probe._remote_ssh22", return_value=eu(*eu_states)),
        ):
            result = compare_ssh22("1.1.1.1")
    assert result["status"] == expected
    assert len(result["local_attempts"]) == 3
    assert len(result["eu_attempts"]) == 3
    assert result["port"] == 22


def test_local_vantage_must_be_confirmed_as_ru():
    env = {**CONFIG, "LESHY_RU_PROBE_CONFIRMED": ""}
    with patch.dict("os.environ", env):
        with (
            patch("leshy.eu_ssh_probe._tcp22", side_effect=local(*(("timeout",) * 3))),
            patch("leshy.eu_ssh_probe._remote_ssh22", return_value=eu(*(("connected",) * 3))),
        ):
            result = compare_ssh22("1.1.1.1")
    assert result["status"] == "local_region_unverified"


@pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.1.1", "169.254.1.1", "203.0.113.1"])
def test_cannot_probe_private_or_reserved_address(ip):
    with patch.dict("os.environ", CONFIG):
        with patch("leshy.eu_ssh_probe._remote_ssh22") as call:
            result = compare_ssh22(ip)
    assert result["status"] == "invalid_target"
    call.assert_not_called()


def test_network_checker_uses_owned_european_result_even_when_cheburcheck_fails():
    checker = NetworkBlockingChecker()
    proof = {
        "status": "ru_unreachable_eu_reachable", "port": 22, "target": "1.1.1.1",
        "local_attempts": [{"status": "timeout"}] * 3,
        "eu_attempts": [{"status": "connected"}] * 3,
        "eu_country_code": "DE",
        "reason": "Сравнение независимых сетей",
    }
    local_results = (
        TcpObservation("local", 22, "timeout"), TcpObservation("local", 3389, "timeout"),
    )
    with (
        patch.object(checker, "_check_local_ports", return_value=local_results),
        patch("leshy.network_blocking_module.collect_service_fingerprint", return_value={
            "ports": [], "os_family": "не определена", "confidence": "отсутствует",
            "evidence": [], "completed": False,
        }),
        patch("leshy.network_blocking_module.eu_ssh_probe_configured", return_value=True),
        patch("leshy.network_blocking_module.compare_ssh22", return_value=proof),
        patch.object(checker, "_check_host", return_value=()),
        patch.object(checker, "_cheburcheck", return_value=CheburcheckEvidence(ok=False)),
        patch.object(checker, "_chebur_probe", return_value=CheburProbeSummary(ok=False)),
    ):
        result = checker.check("1.1.1.1")
    assert "из РФ" in result.verdict
    assert "из Европы" in result.verdict
    assert "механизм ТСПУ не установлен" in result.verdict
    assert result.eu_ssh_comparison["status"] == "ru_unreachable_eu_reachable"
    assert 445 not in [item.port for item in result.local]
    assert 445 not in NetworkBlockingChecker._port_results(result)


def test_server_rejects_private_and_reserved_ips():
    file = Path(__file__).resolve().parents[1] / "tools/eu_ssh_probe_server.py"
    spec = importlib.util.spec_from_file_location("eu_probe_agent_test", file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for ip in ("127.0.0.1", "192.168.1.1", "169.254.1.1", "203.0.113.1"):
        with pytest.raises(ValueError):
            module._validate_target(ip)
    assert module._validate_target("1.1.1.1") == "1.1.1.1"


def test_smb_removed_entirely_from_fingerprint_port_schedule():
    from leshy.network_service_fingerprint import collect_service_fingerprint
    base = [{"port": 22, "status": "refused"}, {"port": 3389, "status": "refused"}]
    with (
        patch("leshy.network_service_fingerprint._public_dns", return_value=True),
        patch("leshy.network_service_fingerprint._probe_port") as probe,
    ):
        result = collect_service_fingerprint("1.1.1.1", base)
    probe.assert_not_called()
    assert [row["port"] for row in result["ports"]] == [22, 3389]
