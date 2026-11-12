import unittest

from leshy.network_blocking_module import (
    CheburProbeObservation,
    CheburProbeSummary,
    CheburcheckEvidence,
    NetworkBlockingChecker,
    TcpObservation,
)


class CheburcheckProbeAssessmentTests(unittest.TestCase):
    def test_cheburcheck_tspu_signal_does_not_prove_blocking(self) -> None:
        local = (
            TcpObservation("local", 22, "timeout"),
            TcpObservation("local", 3389, "refused"),
        )
        remote = (
            TcpObservation("check-host", 22, "pending", country_code="ru", node="ru1"),
        )
        static = CheburcheckEvidence(ok=True, blocked=False, target="203.0.113.10")
        probe = CheburProbeSummary(
            ok=True,
            online_probes=2,
            responded_probes=1,
            observations=(
                CheburProbeObservation(
                    probe_id="42",
                    region="RU",
                    provider="example",
                    verdicts=("tspu_block",),
                    target_hop=None,
                    dpi_hop=7,
                ),
            ),
        )
        result = NetworkBlockingChecker._assessment("203.0.113.10", local, remote, static, probe)
        self.assertEqual(result.tspu_probability, 20)
        self.assertIn("не подтверждена", result.verdict)
        self.assertTrue(any("предварительные" in reason for reason in result.reasons))

    def test_official_ok_reduces_static_list_signal(self) -> None:
        local = (
            TcpObservation("local", 22, "connected"),
            TcpObservation("local", 3389, "refused"),
        )
        static = CheburcheckEvidence(
            ok=True,
            blocked=True,
            target="203.0.113.10",
            blocked_subnets=("203.0.113.0/24",),
        )
        probe = CheburProbeSummary(
            ok=True,
            online_probes=1,
            responded_probes=1,
            observations=(
                CheburProbeObservation(
                    probe_id="7",
                    region="RU",
                    verdicts=("ok",),
                    target_hop=10,
                    dpi_hop=5,
                ),
            ),
        )
        result = NetworkBlockingChecker._assessment("203.0.113.10", local, (), static, probe)
        self.assertLessEqual(result.tspu_probability, 20)
        self.assertTrue(any("Динамический Cheburcheck Probe" in reason for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
