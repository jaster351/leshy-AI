from leshy.verified_tool_receipts import VerifiedToolReceipts


def test_receipt_requires_real_record():
    ledger = VerifiedToolReceipts()
    assert ledger.verify("invented") == {"verified": False}
    receipt = ledger.record("research.search", "success", {"sources": ["https://example.org"]})
    assert ledger.verify(receipt.receipt_id, tool="research.search")["verified"] is True
    assert ledger.verify(receipt.receipt_id, tool="shell.exec") == {"verified": False}
    assert "example.org" not in str(ledger.verify(receipt.receipt_id))


def test_errors_are_not_success_and_ledger_is_bounded():
    ledger = VerifiedToolReceipts(max_entries=1)
    first = ledger.record("research.search", "error", "timeout")
    second = ledger.record("research.search", "approval_required", {})
    assert ledger.verify(first.receipt_id) == {"verified": False}
    assert ledger.verify(second.receipt_id)["status"] == "approval_required"
