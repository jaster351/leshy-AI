import unittest
from unittest.mock import patch

from leshy.modules import ModuleResponse
from leshy.network_blocking_module import (
    CheburProbeObservation,
    CheburProbeSummary,
    CheburcheckEvidence,
    NetworkBlockingChecker,
    TcpObservation,
)
from leshy.network_blocking_runtime import _compact_answer, _run


TEST_IP = "1.1.1.1"


def _local(status="timeout"):
    return (
        TcpObservation("local", 22, status),
        TcpObservation("local", 3389, "refused"),
    )


def _checkhost_ru_foreign():
    return (
        TcpObservation("check-host", 22, "timeout", country_code="ru", node="ru1"),
        TcpObservation("check-host", 22, "connected", country_code="de", node="de1"),
    )


class NetworkBlockingEvidencePolicyTests(unittest.TestCase):
    def test_all_nodes_connection_refused_is_not_tspu_proof(self):
        remote = tuple(
            TcpObservation("check-host", port, "refused", country_code=code)
            for port in (22, 3389)
            for code in ("ru", "de", "nl")
        )
        result = NetworkBlockingChecker._assessment(
            TEST_IP, _local("refused"), remote,
            CheburcheckEvidence(ok=True, blocked=False, target=TEST_IP),
        )
        self.assertEqual(result.tspu_probability, 0)
        self.assertIn("не подтверждена", result.verdict)
        self.assertTrue(any("Connection Refused" in reason for reason in result.reasons))

    def test_combined_evidence_raises_index_without_proving_tspu(self):
        static = CheburcheckEvidence(ok=True, blocked=True, target=TEST_IP)
        probe = CheburProbeSummary(
            ok=True, online_probes=1, responded_probes=1,
            observations=(CheburProbeObservation(probe_id="1", verdicts=("tspu_block",)),),
        )
        combined = NetworkBlockingChecker._assessment(TEST_IP, _local(), _checkhost_ru_foreign(), static, probe)
        single = NetworkBlockingChecker._assessment(TEST_IP, _local(), _checkhost_ru_foreign(),
                                                     CheburcheckEvidence(ok=True, blocked=False, target=TEST_IP))
        self.assertGreater(combined.tspu_probability, single.tspu_probability)
        self.assertLess(combined.tspu_probability, 100)
        self.assertIn("не подтверждена", combined.verdict)

    def test_missing_external_probes_never_imply_no_block(self):
        result = NetworkBlockingChecker._assessment(
            TEST_IP, _local(), (), CheburcheckEvidence(ok=False, error="timeout")
        )
        self.assertLessEqual(result.tspu_probability, 10)
        self.assertTrue(any("не предоставил" in text for text in result.reasons))
        output = NetworkBlockingChecker.format(result)
        self.assertIn("Индекс признаков ТСПУ:", output)
        self.assertIn("Check-Host: результатов нет", output)
        self.assertIn("Статус подтверждения:", output)

    def test_results_have_real_receipts_without_claiming_proof(self):
        checker = NetworkBlockingChecker()
        chebur = CheburcheckEvidence(ok=True, blocked=False, target=TEST_IP, query_id="test-query")
        probe = CheburProbeSummary(ok=True, online_probes=1, responded_probes=1,
                                   observations=(CheburProbeObservation(probe_id="x", verdicts=("ok",)),))
        with (
            patch.object(checker, "_check_local_ports", return_value=_local()),
            patch.object(checker, "_cheburcheck", return_value=chebur),
            patch.object(checker, "_check_host", return_value=_checkhost_ru_foreign()),
            patch.object(checker, "_chebur_probe", return_value=probe),
        ):
            result = checker.check(TEST_IP)
        self.assertEqual(len(result.tool_receipts), 4)
        self.assertEqual({receipt.tool for receipt in result.tool_receipts},
                         {"tcp.connect", "cheburcheck.check", "cheburcheck.probe", "check_host.tcp"})
        self.assertTrue(all(checker._receipts.verify(receipt.receipt_id)["verified"]
                            for receipt in result.tool_receipts))
        self.assertIn("Запуски инструментов", checker.format(result))

    def test_legacy_hundred_percent_heuristic_never_creates_copy_text(self):
        metadata = {"target": TEST_IP, "tspu_probability": 100,
                    "blocking_attribution": "unconfirmed"}
        self.assertIn("не подтверждена", _compact_answer(metadata))
        self.assertNotIn("заблокирован ТСПУ", _compact_answer(metadata))

    def test_run_shows_complete_data_when_attribution_is_unconfirmed(self):
        metadata = {
            "target": TEST_IP, "tspu_probability": 60,
            "blocking_attribution": "unconfirmed",
            "remote": [], "cheburcheck": {"ok": False},
        }
        class FakeModule:
            def handle(self, request):
                return ModuleResponse(handled=True, text="TCP 22: timeout; Check-Host: нет результатов", metadata=metadata)
        with patch("leshy.network_blocking_runtime._module", return_value=FakeModule()):
            answer, sources, result = _run(object(), TEST_IP)
        self.assertIn("TCP 22: timeout", answer)
        self.assertEqual(sources, [])
        self.assertEqual(result["copyable_conclusion"], "")


if __name__ == "__main__":
    unittest.main()
