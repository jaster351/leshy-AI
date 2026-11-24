"""Regression checks for readable, evidence-first TSPU diagnostics."""
import os
from pathlib import Path
from unittest.mock import patch

from leshy.network_blocking_module import (
    CheburcheckEvidence,
    CheburProbeSummary,
    NetworkBlockingChecker,
    TcpObservation,
)
from leshy.network_blocking_runtime import _compact_answer, _run
from leshy.modules import ModuleResponse


def test_missing_probe_token_is_explicit_instead_of_false_negative():
    checker = NetworkBlockingChecker()
    with patch.dict(os.environ, {"LESHY_CHEBURCHECK_API_TOKEN": ""}):
        with patch.object(checker, "_request") as request:
            result = checker._chebur_probe("signed-query-id")
    assert not result.ok
    assert "API-токен" in result.error
    request.assert_not_called()


def test_russian_ssh_reachable_and_rdp_unreachable_is_not_claimed_tspu():
    local = (TcpObservation("local", 22, "timeout"), TcpObservation("local", 3389, "timeout"))
    remote = (
        TcpObservation("check-host", 22, "connected", country_code="ru", node="ru1"),
        TcpObservation("check-host", 22, "connected", country_code="de", node="de1"),
        TcpObservation("check-host", 3389, "timeout", country_code="ru", node="ru1"),
        TcpObservation("check-host", 3389, "refused", country_code="de", node="de1"),
    )
    assessment = NetworkBlockingChecker._assessment(
        "188.130.141.48", local, remote,
        CheburcheckEvidence(ok=True, target="188.130.141.48", blocked=False),
        CheburProbeSummary(ok=False, error="нет API-токена"),
    )
    assert not assessment.three_signal_confirmed
    assert not assessment.matched_ports
    assert "Недостаточно данных" in assessment.verdict
    assert "65%" not in NetworkBlockingChecker.format(assessment)


def test_verified_listing_is_reported_even_without_3_of_3():
    assessment = NetworkBlockingChecker._assessment(
        "1.1.1.1", (TcpObservation("local", 22, "timeout"),), (),
        CheburcheckEvidence(ok=True, target="1.1.1.1", blocked=True),
    )
    assert "найден в блокировочных списках" in assessment.verdict
    assert not assessment.three_signal_confirmed


def test_chat_keeps_complete_diagnostics_in_metadata():
    metadata = {
        "target": "188.130.141.48",
        "verdict": "Недостаточно данных для вывода о наличии или отсутствии блокировки",
        "blocking_attribution": "inconclusive",
        "cheburcheck": {"ok": True, "blocked": False},
        "chebur_probe": {"ok": False, "error": "нужен API-токен"},
        "local": [], "remote": [],
    }
    class Module:
        def handle(self, request):
            return ModuleResponse(
                handled=True, text="Проверка 188.130.141.48\\nПротокол TCP/22",
                metadata=metadata,
            )
    with patch("leshy.network_blocking_runtime._module", return_value=Module()):
        answer, _, result = _run(object(), "188.130.141.48")
    assert answer.startswith("188.130.141.48 — не удалось однозначно")
    assert "[[LESHY_DIAGNOSTICS]]" not in answer
    assert "Протокол TCP/22" not in answer
    assert "Протокол TCP/22" in result["diagnostic_report"]


def test_ui_and_telegram_support_expanding_report():
    root = Path(__file__).resolve().parents[1]
    assert "leshy-diagnostic-details" in (root / "web/chat-extras.js").read_text(encoding="utf-8")
    assert "leshy-diagnostic-details" in (root / "web/index.html").read_text(encoding="utf-8")
    assert "<blockquote expandable>" in (root / "src/leshy/telegram_rich.py").read_text(encoding="utf-8")
