"""Pure offline regression tests for concise two-port diagnostic verdicts."""
import unittest

from leshy.network_blocking_verdict import assess_ports
from leshy.network_blocking_module import (
    CheburcheckEvidence, CheburProbeObservation, CheburProbeSummary, TcpObservation,
)


def pair(port, local, abroad, *, status="inconclusive"):
    return {
        "port": port, "status": status, "eu_country_code": "NL",
        "local_attempts": [{"status": item} for item in local],
        "eu_attempts": [{"status": item} for item in abroad],
    }


class TspuVerdictTests(unittest.TestCase):
    def test_confirmed_all_signals_give_requested_ip_level_phrase(self):
        own = {"ports": {
            "22": pair(22, ["timeout"] * 3, ["connected"] * 3),
            "3389": pair(3389, ["connected"] * 3, ["connected"] * 3),
        }}
        checks = (
            TcpObservation("check-host", 22, "timeout", country_code="ru", node="ru1"),
            TcpObservation("check-host", 22, "connected", country_code="nl", node="nl1"),
        )
        probe = CheburProbeSummary(
            ok=True, finished=True,
            observations=(CheburProbeObservation("probe1", verdicts=("tspu_block",)),),
        )
        verdict, ports = assess_ports(own, checks, chebur_probe=probe)
        self.assertIn("Обнаружена блокировка на стороне ТСПУ", verdict)
        self.assertEqual(ports["22"]["state"], "tspu_detected")
        self.assertEqual(ports["3389"]["state"], "not_blocked")

    def test_same_diagnosis_without_chebur_token_if_checkhost_agrees(self):
        own = {"ports": {
            "22": pair(22, ["timeout"] * 3, ["connected"] * 3),
            "3389": pair(3389, ["refused"] * 3, ["refused"] * 3),
        }}
        checks = (
            TcpObservation("check-host", 22, "timeout", country_code="ru", node="ru1"),
            TcpObservation("check-host", 22, "connected", country_code="nl", node="nl1"),
        )
        verdict, ports = assess_ports(
            own, checks,
            cheburcheck=CheburcheckEvidence(ok=True, blocked=True),
        )
        self.assertIn("Обнаружена блокировка на стороне ТСПУ", verdict)
        self.assertEqual(ports["22"]["state"], "tspu_detected")

    def test_check_host_russian_success_prevents_tspu_assertion(self):
        own = {"ports": {
            "22": pair(22, ["timeout"] * 3, ["connected"] * 3),
            "3389": pair(3389, ["timeout"] * 3, ["timeout"] * 3),
        }}
        checks = (
            TcpObservation("check-host", 22, "timeout", country_code="ru", node="ru1"),
            TcpObservation("check-host", 22, "connected", country_code="ru", node="ru2"),
            TcpObservation("check-host", 22, "connected", country_code="nl", node="nl1"),
        )
        verdict, ports = assess_ports(own, checks)
        self.assertIn("Возможное ограничение доступа", verdict)
        self.assertNotIn("Обнаружена блокировка на стороне ТСПУ", verdict)
        self.assertEqual(ports["22"]["state"], "possible_restriction")

    def test_both_ports_local_success_mean_not_detected(self):
        own = {"ports": {
            "22": pair(22, ["connected"] * 3, ["connected"] * 3),
            "3389": pair(3389, ["connected"] * 3, ["connected"] * 3),
        }}
        verdict, ports = assess_ports(own, ())
        self.assertIn("Блокировка не обнаружена на стороне ТСПУ", verdict)
        self.assertEqual([ports[p]["state"] for p in ("22", "3389")], ["not_blocked", "not_blocked"])

    def test_timeout_both_sides_is_not_tspu_evidence(self):
        own = {"ports": {
            "22": pair(22, ["timeout"] * 3, ["timeout"] * 3),
            "3389": pair(3389, ["timeout"] * 3, ["timeout"] * 3),
        }}
        verdict, ports = assess_ports(own, ())
        self.assertIn("Недостаточно данных", verdict)
        self.assertTrue(all(x["state"] == "unavailable_both" for x in ports.values()))

    def test_one_accessible_and_one_unavailable_both_is_clear(self):
        for active, inactive in ((22, 3389), (3389, 22)):
            own = {"ports": {
                str(active): pair(active, ["connected"] * 3, ["connected"] * 3),
                str(inactive): pair(inactive, ["timeout"] * 3, ["refused"] * 3),
            }}
            verdict, ports = assess_ports(own, ())
            self.assertIn("Признаки блокировки на стороне ТСПУ не обнаружены", verdict)
            self.assertEqual(ports[str(inactive)]["state"], "unavailable_both")
            for status in ("pending", "error"):
                own["ports"][str(inactive)]["eu_attempts"][0]["status"] = status
                verdict, ports = assess_ports(own, ())
                self.assertEqual(ports[str(inactive)]["state"], "insufficient")
            own["ports"][str(inactive)] = pair(inactive, ["timeout"] * 3, ["timeout"] * 3)
            verdict, ports = assess_ports(own, (
                TcpObservation("check-host", inactive, "connected", country_code="de", node="de1"),
            ))
            self.assertEqual(ports[str(inactive)]["state"], "insufficient")

    def test_stale_country_prevents_confirmed_attribution(self):
        own = {"ports": {
            "22": pair(22, ["timeout"] * 3, ["connected"] * 3, status="local_region_unverified"),
            "3389": pair(3389, ["timeout"] * 3, ["timeout"] * 3),
        }}
        verdict, ports = assess_ports(own, ())
        self.assertEqual(ports["22"]["state"], "possible_restriction")
        self.assertNotIn("Обнаружена блокировка на стороне ТСПУ", verdict)


if __name__ == "__main__":
    unittest.main()
