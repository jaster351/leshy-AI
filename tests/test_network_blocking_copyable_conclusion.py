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
    assert _compact_answer(metadata) == f"{IP} Обнаружена блокировка на стороне ТСПУ."


def test_legacy_score_of_100_is_not_proof():
    assert "Не удалось однозначно" in _compact_answer({"target": IP, "tspu_probability": 100})


def test_claim_requires_verified_proof_record():
    metadata = {"target": IP, "blocking_attribution": "confirmed_tspu",
                "proof_verification": "verified"}
    assert "Не удалось однозначно" in _compact_answer(metadata)


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
def test_positive_port_requires_completed_independent_probe():
    metadata = {"target": IP, "port_verdicts": _ports("tspu_detected", "insufficient")}
    assert "Не удалось однозначно" in _compact_answer(metadata)
    metadata["chebur_probe"] = _probe()
    assert _compact_answer(metadata) == f"{IP} Обнаружена блокировка на стороне ТСПУ."


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
def test_negative_requires_both_ports_without_contradictory_evidence():
    metadata = {"target": IP, "port_verdicts": _ports()}
    assert _compact_answer(metadata) == f"{IP} Блокировка не обнаружена."
    metadata["chebur_probe"] = _probe()
    assert "противоречат" in _compact_answer(metadata)
    assert "Не удалось однозначно" in _compact_answer(
        {"target": IP, "port_verdicts": _ports("not_blocked", "insufficient")})


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
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


def test_uncertain_result_lists_actual_country_and_owned_probe_results():
    metadata = {
        "target": IP, "port_verdicts": _ports("insufficient", "insufficient"),
        "eu_ssh_comparison": {"ports": {
            "22": {"local_attempts": [{"status": "timeout"}] * 3,
                   "eu_attempts": [{"status": "connected"}] * 3},
            "3389": {"local_attempts": [{"status": "refused"}] * 3,
                     "eu_attempts": [{"status": "refused"}] * 3},
        }},
        "remote": [
            {"port": 22, "country_code": "ru", "status": "timeout"},
            {"port": 22, "country_code": "de", "status": "connected"},
            {"port": 22, "country_code": "nl", "status": "pending"},
            {"port": 3389, "country_code": "ru", "status": "refused"},
            {"port": 3389, "country_code": "de", "status": "error"},
        ],
        "cheburcheck": {"ok": True, "blocked": False},
    }
    with patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"}):
        answer = _compact_answer(metadata)
    assert "Подключение из России: SSH/22: нет; RDP/3389: нет." in answer
    assert "Подключение с Вашего EU-сервера: SSH/22: есть; RDP/3389: нет." in answer
    assert "SSH/22: есть: Германия; нет: Россия" in answer
    assert "RDP/3389: нет: Россия" in answer
    assert "Cheburcheck (блокировка в реестре): нет." in answer
    assert "нет данных" not in answer
    assert "Нидерланды" not in answer


def test_unknown_geography_and_failed_checks_are_not_negative_results():
    metadata = {
        "target": IP,
        "local": [{"port": 22, "status": "connected"}],
        "remote": [{"port": 22, "country_code": "ru", "status": "error"}],
        "cheburcheck": {"ok": False, "blocked": False},
    }
    with patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "0"}):
        answer = _compact_answer(metadata)
    assert answer == f"{IP} Не удалось однозначно определить наличие блокировки на уровне ТСПУ."
    assert "нет данных" not in answer


def test_checkhost_mixed_country_is_not_a_binary_negative():
    from leshy.network_blocking_runtime import _checkhost_countries
    rows = [{"country_code": "ru", "status": "connected"},
            {"country_code": "ru", "status": "timeout"}]
    assert _checkhost_countries(rows) == "частично: Россия"


def test_unfinished_measurements_never_create_negative_result():
    from leshy.network_blocking_runtime import _connection_status
    assert _connection_status([{"status": "pending"}]) is None
    assert _connection_status([{"status": "timeout"}, {"status": "error"}]) is None
    assert _connection_status([{"status": "connected"}, {"status": "pending"}]) == "есть"
    assert _connection_status([{"status": "refused"}, {"status": "timeout"}]) == "нет"


def test_no_decisive_country_verdict_without_verified_russian_vantage():
    with patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "0"}):
        assert "Не удалось однозначно" in _compact_answer({"target": IP, "port_verdicts": _ports()})
        assert "Не удалось однозначно" in _compact_answer({
            "target": IP, "port_verdicts": _ports("tspu_detected", "insufficient"),
            "chebur_probe": _probe(),
        })


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
def test_positive_registry_is_not_hidden_by_accessible_ports():
    answer = _compact_answer({"target": IP, "port_verdicts": _ports(),
                              "cheburcheck": {"ok": True, "blocked": True}})
    assert "Не удалось однозначно" in answer
    assert "Cheburcheck (блокировка в реестре): есть." in answer


def _owned_geographic_evidence():
    return {
        "target": IP,
        "port_verdicts": _ports("likely_blocked", "insufficient"),
        "eu_ssh_comparison": {"ports": {
            "22": {"status": "ru_unreachable_eu_reachable",
                   "local_attempts": [{"status": "timeout"}] * 3,
                   "eu_attempts": [{"status": "connected"}] * 3},
            "3389": {"local_attempts": [{"status": "refused"}] * 3,
                     "eu_attempts": [{"status": "refused"}] * 3},
        }},
        "remote": [
            {"port": 22, "country_code": "de", "status": "connected"},
            {"port": 22, "country_code": "nl", "status": "connected"},
            {"port": 3389, "country_code": "de", "status": "refused"},
            {"port": 3389, "country_code": "nl", "status": "refused"},
        ],
        "cheburcheck": {"ok": True, "blocked": False},
    }


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
def test_owned_geographic_evidence_is_sufficient_without_chebur_probe():
    metadata = _owned_geographic_evidence()
    assert _compact_answer(metadata) == f"{IP} Обнаружена блокировка на стороне ТСПУ."


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
def test_geographic_rule_rejects_refusal_and_conflicting_russian_success():
    metadata = _owned_geographic_evidence()
    metadata["eu_ssh_comparison"]["ports"]["22"]["local_attempts"] = [{"status": "refused"}] * 3
    assert "Не удалось однозначно" in _compact_answer(metadata)
    metadata = _owned_geographic_evidence()
    metadata["remote"].append({"port": 22, "country_code": "ru", "status": "connected"})
    assert "Не удалось однозначно" in _compact_answer(metadata)


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
def test_geographic_rule_requires_two_distinct_countries_on_same_port():
    metadata = _owned_geographic_evidence()
    metadata["remote"][1]["country_code"] = "de"
    assert "Не удалось однозначно" in _compact_answer(metadata)
    metadata = _owned_geographic_evidence()
    metadata["remote"][1]["port"] = 3389
    assert "Не удалось однозначно" in _compact_answer(metadata)


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "0"})
def test_geographic_rule_requires_verified_russian_exit():
    assert "Не удалось однозначно" in _compact_answer(_owned_geographic_evidence())


@patch.dict("os.environ", {"LESHY_RU_PROBE_CONFIRMED": "1"})
def test_geographic_rule_keeps_explicit_clear_probe_as_conflicting_evidence():
    metadata = _owned_geographic_evidence()
    metadata["chebur_probe"] = {"ok": True, "finished": True,
                               "observations": [{"verdicts": ["ok"]}]}
    assert "Не удалось однозначно" in _compact_answer(metadata)
