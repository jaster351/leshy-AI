"""Verify that only completed, same-port evidence produces the operator's 3/3 verdict.

Fixtures here are unit-test data, never live diagnostic results.
"""
import unittest

from leshy.network_blocking_module import (
    CheburProbeObservation,
    CheburProbeSummary,
    CheburcheckEvidence,
    NetworkBlockingChecker,
    TcpObservation,
)
from leshy.network_blocking_runtime import _compact_answer


TARGET = "1.1.1.1"


def local(ssh="timeout", rdp="refused"):
    return (
        TcpObservation("local", 22, ssh),
        TcpObservation("local", 3389, rdp),
    )


def remote(ssh_ru="timeout", rdp_ru="refused"):
    return (
        TcpObservation("check-host", 22, ssh_ru, country_code="ru", node="ru1"),
        TcpObservation("check-host", 22, "connected", country_code="de", node="de1"),
        TcpObservation("check-host", 3389, rdp_ru, country_code="ru", node="ru1"),
        TcpObservation("check-host", 3389, "refused", country_code="de", node="de1"),
    )


def probe(*, finished=True, verdict="tspu_block"):
    return CheburProbeSummary(
        ok=finished, finished=finished,
        online_probes=1, responded_probes=1,
        observations=(CheburProbeObservation(probe_id="check-1", region="RU", verdicts=(verdict,)),),
    )


def assess(local_results=None, remote_results=None, chebur_probe=None):
    return NetworkBlockingChecker._assessment(
        TARGET,
        local() if local_results is None else local_results,
        remote() if remote_results is None else remote_results,
        CheburcheckEvidence(ok=True, blocked=False, target=TARGET),
        probe() if chebur_probe is None else chebur_probe,
    )


class ThreeSignalBlockingTests(unittest.TestCase):
    def test_complete_same_port_three_signals_confirm(self):
        result = assess()
        self.assertTrue(result.three_signal_confirmed)
        self.assertEqual(result.signals_met, 3)
        self.assertEqual(result.matched_ports, (22,))
        self.assertTrue(result.check_host_complete)
        self.assertIn("3/3", result.verdict)
        self.assertIn("подтверждена", result.verdict)
        self.assertIn("3/3", NetworkBlockingChecker.format(result))

    def test_partial_check_host_must_not_confirm(self):
        rows = tuple(
            TcpObservation(**{
                "source": item.source, "port": item.port, "status": "pending"
                if item.node == "de1" and item.port == 3389 else item.status,
                "country_code": item.country_code, "node": item.node,
            })
            for item in remote()
        )
        result = assess(remote_results=rows)
        self.assertFalse(result.check_host_complete)
        self.assertFalse(result.three_signal_confirmed)
        self.assertNotIn("подтверждена по трём", result.verdict)

    def test_sse_without_done_is_not_a_confirmed_tspu_probe(self):
        result = assess(chebur_probe=probe(finished=False))
        self.assertFalse(result.three_signal_confirmed)
        self.assertEqual(result.signals_met, 2)
        self.assertIn("2/3", result.verdict)

    def test_mismatched_ports_do_not_combine(self):
        result = assess(remote_results=remote(ssh_ru="connected", rdp_ru="timeout"))
        self.assertFalse(result.three_signal_confirmed)
        self.assertEqual(result.signals_met, 2)
        self.assertEqual(result.matched_ports, ())

    def test_connection_refused_is_not_direct_timeout(self):
        result = assess(local_results=local("refused", "refused"))
        self.assertFalse(result.three_signal_confirmed)
        self.assertNotEqual(result.signals_met, 3)

    def test_legacy_score_not_a_three_signal_verdict(self):
        self.assertIn("не подтверждена", _compact_answer({
            "target": TARGET, "tspu_probability": 99,
            "blocking_attribution": "unconfirmed",
        }))

    def test_three_signal_summary_reports_port_and_node_count(self):
        metadata = {
            "target": TARGET,
            "blocking_attribution": "confirmed_three_signals",
            "matched_ports": [22],
            "remote": [
                {"port": 22, "country_code": "ru", "status": "timeout"},
                {"port": 22, "country_code": "ru", "status": "connected"},
            ],
            "chebur_probe": {"observations": [{"verdicts": ["tspu_block"]}]},
        }
        text = _compact_answer(metadata)
        self.assertIn("3/3", text)
        self.assertIn("TCP/22", text)
        self.assertIn("1 timeout из 2", text)
        self.assertIn("tspu_block", text)

    def test_chebur_done_requires_response_count_parsing(self):
        state = {"observations": [], "online_probes": 0}
        completed = NetworkBlockingChecker._dispatch_sse_event(
            "done", ['{"response_count": 2, "online_probes": 3}'], state,
        )
        self.assertTrue(completed)
        self.assertTrue(state["finished"])
        self.assertEqual(state["responded_probes"], 2)


if __name__ == "__main__":
    unittest.main()
