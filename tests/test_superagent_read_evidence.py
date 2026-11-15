"""Tool ledger verification and private SuperAgent evidence rendering.

The receipt proves a local tool was called, not source truth or a signed
persistent audit trail. No test executes network, shell, or the real LLM.
"""
from __future__ import annotations

from hashlib import sha256
import json
import unittest
from unittest.mock import Mock

from leshy.superagent_dashboard import ticket_details
from leshy.superagent_read_evidence import (
    read_result_digest,
    summarize_plan_evidence,
    verify_live_receipt,
)
from leshy.verified_tool_receipts import VerifiedToolReceipts
from test_superagent_dashboard import FakeService


class ReceiptEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.ledger = VerifiedToolReceipts()
        self.reader = Mock()
        self.reader.receipts = self.ledger
        self.actual = {"version": "1.10.139"}
        self.record = self.ledger.record("runtime.version", "success", self.actual)
        self.response = {"tool": "runtime.version", "status": "success",
                         "receipt_id": self.record.receipt_id, "result": self.actual}

    def verified_evidence(self, *, result=None, truncated=False):
        result = self.actual if result is None else result
        evidence = {
            "tool": "runtime.version",
            "receipt_id": self.record.receipt_id,
            "result_sha256": read_result_digest(result),
            "verified_receipt": True,
            "recorded_at": self.record.timestamp_utc,
        }
        if truncated:
            evidence["result_truncated"] = True
        else:
            evidence["result"] = result
        return {"state": "done", "tool": "runtime.version", "output": json.dumps(evidence)}

    def test_real_receipt_and_digest(self):
        verified = verify_live_receipt(self.reader, self.response, "runtime.version")
        self.assertEqual(verified["result_sha256"], self.record.result_sha256)
        self.assertEqual(verified["recorded_at"], self.record.timestamp_utc)
        evidence = summarize_plan_evidence(self.verified_evidence())
        self.assertEqual(evidence["status"], "recorded")
        self.assertEqual(evidence["summary"], "Версия: 1.10.139")
        self.assertNotIn("result", evidence)

    def test_missing_forged_wrong_tool_and_modified_result_fail(self):
        for reader, response, tool in [
            (Mock(receipts=None), self.response, "runtime.version"),
            (self.reader, {**self.response, "receipt_id": "a" * 32}, "runtime.version"),
            (self.reader, self.response, "runtime.platform"),
            (self.reader, {**self.response, "result": {"version": "counterfeit"}},
             "runtime.version"),
        ]:
            with self.subTest(tool=tool, receipt=response["receipt_id"]):
                with self.assertRaises(RuntimeError):
                    verify_live_receipt(reader, response, tool)

    def test_tampering_old_and_truncated_results_are_honest(self):
        forged = self.verified_evidence(result={"version": "forged"})
        data = json.loads(forged["output"])
        data["result_sha256"] = "0" * 64
        forged["output"] = json.dumps(data)
        self.assertEqual(summarize_plan_evidence(forged)["status"], "unavailable")
        self.assertEqual(summarize_plan_evidence(self.verified_evidence(truncated=True))[
            "status"], "recorded")
        bad_truncated = self.verified_evidence()
        data = json.loads(bad_truncated["output"])
        data["result_truncated"] = True
        bad_truncated["output"] = json.dumps(data)
        self.assertEqual(summarize_plan_evidence(bad_truncated)["status"], "unavailable")
        self.assertEqual(summarize_plan_evidence({
            "state": "done", "tool": "runtime.version",
            "output": json.dumps({"tool": "runtime.version",
                                  "receipt_id": self.record.receipt_id,
                                  "result": self.actual}),
        })["status"], "legacy")

    def test_no_private_query_claims_urls_or_manual_steps(self):
        secrets = {"count": 1, "items": [{"claim": "PRIVATE_VALUE",
                 "source_url": "https://site.invalid/?token=SENSITIVE"}]}
        receipt = self.ledger.record("knowledge.search", "success", secrets)
        item = summarize_plan_evidence({
            "state": "done", "tool": "knowledge.search",
            "output": json.dumps({
                "tool": "knowledge.search", "result": secrets,
                "receipt_id": receipt.receipt_id,
                "result_sha256": receipt.result_sha256,
                "recorded_at": receipt.timestamp_utc,
                "verified_receipt": True,
            }),
        })
        serialized = json.dumps(item)
        self.assertEqual(item["summary"], "Найдено проверенных записей: 1")
        self.assertNotIn("PRIVATE_VALUE", serialized)
        self.assertNotIn("SENSITIVE", serialized)
        self.assertIsNone(summarize_plan_evidence({
            "state": "done", "tool": "", "output": "PRIVATE_VALUE",
        }))

    def test_dashboard_owner_only_no_raw_output(self):
        service = FakeService()
        service.plans.snapshot = Mock(return_value={
            "state": "completed", "auto_run": False,
            "steps": [{"id": "one", "title": "Find version",
                       **self.verified_evidence()}],
        })
        detail = ticket_details(service, "web:alice", "SA-123")["task"]
        result = detail["plan"]["steps"][0]
        self.assertEqual(result["evidence"]["status"], "recorded")
        self.assertEqual(result["evidence"]["summary"], "Версия: 1.10.139")
        self.assertNotIn("output", result)
        with self.assertRaises(PermissionError):
            ticket_details(service, "web:bob", "SA-123")


if __name__ == "__main__":
    unittest.main()
