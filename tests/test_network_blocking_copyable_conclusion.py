from leshy.network_blocking_runtime import _compact_answer


def test_copyable_confirmed_statement_has_no_colon_after_ip():
    metadata = {
        "target": "161.104.90.30",
        "blocking_attribution": "confirmed_tspu",
        "proof_verification": "verified",
        "proof_record_id": "trusted-evidence-42",
    }
    assert _compact_answer(metadata) == "IP-адрес 161.104.90.30 заблокирован ТСПУ."


def test_legacy_score_of_100_is_not_proof():
    metadata = {"target": "161.104.90.30", "tspu_probability": 100}
    assert _compact_answer(metadata) == "IP-адрес 161.104.90.30. Блокировка ТСПУ не подтверждена."


def test_claim_requires_verified_proof_record():
    metadata = {
        "target": "161.104.90.30",
        "blocking_attribution": "confirmed_tspu",
        "proof_verification": "verified",
    }
    assert "заблокирован ТСПУ" not in _compact_answer(metadata)
