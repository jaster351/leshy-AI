"""Offline independent review of Knowledge citations and owner-only UI summaries."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from leshy.durable_plan_store import DurablePlanStore
from leshy.superagent_source_reviewer import review_knowledge_sources
from leshy.superagent_read_evidence import read_result_digest, summarize_plan_evidence
from leshy.verified_tool_receipts import VerifiedToolReceipts
from leshy.superagent_dashboard import ticket_details
from test_superagent_dashboard import FakeService


def knowledge(items):
    return {"items": items, "count": len(items)}


def entry(claim, url):
    return {"topic": "topic", "claim": claim, "source_url": url}


class SourceReviewerTests(unittest.TestCase):
    def test_multiple_domains_without_claiming_truth(self):
        rows = knowledge([
            entry("One", "https://docs.python.org/3/"),
            entry("Two", "https://www.rfc-editor.org/rfc/"),
        ])
        result = review_knowledge_sources(rows)
        self.assertEqual((result["status"], result["linked_items"],
                          result["distinct_hosts"]), ("multiple_hosts", 2, 2))
        self.assertFalse(result["fact_checked"])
        self.assertNotIn("docs.python.org", json.dumps(result))
        self.assertNotIn("One", json.dumps(result))

    def test_one_site_or_duplicate_claim_is_not_independent(self):
        for items in (
            [entry("one", "https://www.example.org/a")],
            [entry("same", "https://example.org/a"),
             entry(" same ", "https://other.example.net/b")],
            [entry("one", "https://example.org/a"),
             entry("two", "")],
        ):
            with self.subTest(items=items):
                self.assertEqual(review_knowledge_sources(knowledge(items))["status"],
                                 "limited_sources")

    def test_empty_and_invalid_links(self):
        self.assertEqual(review_knowledge_sources(knowledge([]))["status"], "empty")
        rows = knowledge([entry("one", "file:///etc/passwd"),
                          entry("two", "https://user:secret@example.com/path")])
        reviewed = review_knowledge_sources(rows)
        self.assertEqual(reviewed["status"], "missing_sources")
        self.assertEqual(reviewed["missing_links"], 2)
        self.assertNotIn("secret", json.dumps(reviewed))

    def test_malformed_result_is_not_reviewed(self):
        for invalid in ([], None, {}, {"count": 2, "items": []},
                        {"count": True, "items": []},
                        {"count": 1, "items": [None]},
                        knowledge([{"claim": "not enough metadata"}])):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    review_knowledge_sources(invalid)

    def test_stored_review_is_recomputed_and_never_exposes_urls(self):
        rows = knowledge([
            entry("PRIVATE CLAIM", "https://first.example.com/?token=PRIVATE"),
            entry("Different fact", "https://second.example.org/?secret=PASSWORD"),
        ])
        receipt = VerifiedToolReceipts().record("knowledge.search", "success", rows)
        content = {
            "tool": "knowledge.search", "receipt_id": receipt.receipt_id,
            "result_sha256": read_result_digest(rows),
            "recorded_at": receipt.timestamp_utc, "verified_receipt": True,
            "result": rows, "source_review": review_knowledge_sources(rows),
        }
        step = {"id": "one", "title": "Search", "tool": "knowledge.search",
                "state": "done", "output": json.dumps(content)}
        summarized = summarize_plan_evidence(step)
        self.assertEqual(summarized["source_review"]["status"], "multiple_hosts")
        self.assertEqual(summarized["source_review_status"], "reviewed")
        private_text = json.dumps(summarized)
        for secret in ("PRIVATE CLAIM", "PRIVATE", "PASSWORD", "first.example.com"):
            self.assertNotIn(secret, private_text)

        svc = FakeService()
        svc.plans.snapshot = Mock(return_value={
            "state": "completed", "auto_run": False, "steps": [step],
        })
        detail = ticket_details(svc, "web:alice", "SA-123")["task"]["plan"]["steps"][0]
        self.assertIn("source_review", detail["evidence"])
        self.assertNotIn("output", detail)
        with self.assertRaises(PermissionError):
            ticket_details(svc, "web:bob", "SA-123")

        changed = dict(content)
        changed["source_review"] = {**content["source_review"], "distinct_hosts": 100}
        step["output"] = json.dumps(changed)
        wrong = summarize_plan_evidence(step)
        self.assertEqual(wrong["source_review_status"], "unavailable")
        self.assertNotIn("source_review", wrong)

    def test_legacy_receipt_without_review_does_not_claim_review(self):
        old = {"tool": "knowledge.search", "receipt_id": "f" * 32,
               "result": knowledge([]), "verified_receipt": True}
        old["result_sha256"] = read_result_digest(old["result"])
        step = {"tool": "knowledge.search", "state": "done", "output": json.dumps(old)}
        summary = summarize_plan_evidence(step)
        self.assertNotIn("source_review", summary)

    def test_pause_rejects_inflight_completion_until_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DurablePlanStore(Path(temp) / "brain.sqlite")
            ident = store.create("alice", [{
                "id": "a", "title": "Version", "tool": "runtime.version",
            }], plan_id="SA-paused")
            claim = store.claim(ident, "alice", now=100, lease_seconds=300)[0]
            store.control(ident, "alice", "pause")
            self.assertFalse(store.complete(
                ident, "alice", claim["id"], claim["token"],
                success=True, now=101))
            self.assertEqual(store.snapshot(ident, "alice")["steps"][0]["state"],
                             "running")
            store.control(ident, "alice", "resume")
            self.assertTrue(store.complete(
                ident, "alice", claim["id"], claim["token"],
                success=True, now=102))


if __name__ == "__main__":
    unittest.main()
