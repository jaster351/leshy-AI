from unittest.mock import patch

from leshy.modules import ModuleResponse
from leshy.network_blocking_runtime import _compact_answer, _run

IP = "85.137.251.10"


def _ports(first="not_blocked", second="not_blocked"):
    return {"22": {"state": first, "explanation": "Недостаточно данных"},
            "3389": {"state": second, "explanation": "Недостаточно данных"}}


def _probe():
    return {"ok": True, "finished": True, "observations": [{"verdicts": ["tspu_block"]}]}


def test_copyable_confirmed_statement():
    metadata = {"target": IP, "blocking_attribution": "confirmed_tspu",
                "proof_verification": "verified", "proof_record_id": "trusted-evidence-42"}
    assert _compact_answer(metadata) == f"{IP} обнаружена блокировка на уровне ТСПУ."


def test_legacy_score_of_100_is_not_proof():
    assert "Не удалось однозначно" in _compact_answer({"target": IP, "tspu_probability": 100})


def test_claim_requires_verified_proof_record():
    metadata = {"target": IP, "blocking_attribution": "confirmed_tspu",
                "proof_verification": "verified"}
    assert "Не удалось однозначно" in _compact_answer(metadata)


def test_positive_port_requires_completed_independent_probe():
    metadata = {"target": IP, "port_verdicts": _ports("tspu_detected", "insufficient")}
    assert "Не удалось однозначно" in _compact_answer(metadata)
    metadata["chebur_probe"] = _probe()
    assert _compact_answer(metadata) == f"{IP} обнаружена блокировка на уровне ТСПУ."


def test_negative_requires_both_ports_without_contradictory_evidence():
    metadata = {"target": IP, "port_verdicts": _ports()}
    assert _compact_answer(metadata) == f"{IP} Блокировка не обнаружена."
    metadata["chebur_probe"] = _probe()
    assert "противоречат" in _compact_answer(metadata)
    assert "Не удалось однозначно" in _compact_answer(
        {"target": IP, "port_verdicts": _ports("not_blocked", "insufficient")})


def test_only_conclusion_is_delivered_in_chat():
    metadata = {"target": IP, "port_verdicts": _ports(), "verdict": "long summary",
                "cheburcheck": {"ok": True, "source_url": "https://cheburcheck.ru/"}}
    class Module:
        def handle(self, request):
            return ModuleResponse(handled=True, text="Full TCP protocol", metadata=metadata)
    with patch("leshy.network_blocking_runtime._module", return_value=Module()):
        answer, sources, result = _run(object(), IP)
    assert answer == f"{IP} Блокировка не обнаружена."
    assert sources == []
    assert result["copyable_conclusion"] == answer
    assert result["diagnostic_report"] == "Full TCP protocol"
