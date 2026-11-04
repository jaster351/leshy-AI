import unittest

from leshy.network_blocking_module import (
    CheburcheckEvidence,
    NetworkBlockingChecker,
    TcpObservation,
)


class NetworkBlockingModuleTests(unittest.TestCase):
    def test_plain_ip_is_recognized_without_os_question(self) -> None:
        self.assertTrue(NetworkBlockingChecker.looks_like_request("203.0.113.10"))
        self.assertEqual(NetworkBlockingChecker.extract_target("Проверь блокировку 203.0.113.10"), "203.0.113.10")

    def test_ssh_only_infers_linux_service_profile(self) -> None:
        local = (
            TcpObservation("local", 22, "connected"),
            TcpObservation("local", 3389, "refused"),
        )
        profile, ports = NetworkBlockingChecker._service_profile(local, ())
        self.assertEqual(profile, "Linux/SSH")
        self.assertEqual(ports, (22,))

    def test_rdp_only_infers_windows_service_profile(self) -> None:
        local = (
            TcpObservation("local", 22, "refused"),
            TcpObservation("local", 3389, "connected"),
        )
        profile, ports = NetworkBlockingChecker._service_profile(local, ())
        self.assertEqual(profile, "Windows/RDP")
        self.assertEqual(ports, (3389,))

    def test_ru_timeout_and_foreign_success_is_only_indirect_signal(self) -> None:
        local = (
            TcpObservation("local", 22, "timeout"),
            TcpObservation("local", 3389, "refused"),
        )
        remote = (
            TcpObservation("check-host", 22, "timeout", country_code="ru", country="Russia", city="Moscow", node="ru-node"),
            TcpObservation("check-host", 22, "connected", country_code="de", country="Germany", city="Frankfurt", node="de-node"),
            TcpObservation("check-host", 3389, "refused", country_code="ru", country="Russia", city="Moscow", node="ru-node"),
            TcpObservation("check-host", 3389, "refused", country_code="de", country="Germany", city="Frankfurt", node="de-node"),
        )
        chebur = CheburcheckEvidence(ok=True, blocked=False, target="203.0.113.10")
        result = NetworkBlockingChecker._assessment("203.0.113.10", local, remote, chebur)
        self.assertEqual(result.profile, "Linux/SSH")
        self.assertEqual(result.service_ports, (22,))
        self.assertEqual(result.tspu_probability, 45)
        self.assertIn("не подтверждена", result.verdict)
        self.assertIn("ТСПУ", result.verdict)
        self.assertTrue(any("узел Check-Host в РФ" in reason for reason in result.reasons))

    def test_ru_and_foreign_success_do_not_indicate_tspu(self) -> None:
        local = (
            TcpObservation("local", 22, "connected"),
            TcpObservation("local", 3389, "refused"),
        )
        remote = (
            TcpObservation("check-host", 22, "connected", country_code="ru", country="Russia", city="Moscow", node="ru-node"),
            TcpObservation("check-host", 22, "connected", country_code="fi", country="Finland", city="Helsinki", node="fi-node"),
        )
        chebur = CheburcheckEvidence(ok=True, blocked=False, target="203.0.113.10")
        result = NetworkBlockingChecker._assessment("203.0.113.10", local, remote, chebur)
        self.assertLessEqual(result.tspu_probability, 20)
        self.assertIn("не подтверждена", result.verdict.lower())

    def test_cheburcheck_listing_without_geographic_proof_is_probabilistic(self) -> None:
        local = (
            TcpObservation("local", 22, "timeout"),
            TcpObservation("local", 3389, "timeout"),
        )
        chebur = CheburcheckEvidence(
            ok=True,
            blocked=True,
            target="203.0.113.10",
            blocked_subnets=("203.0.113.0/24",),
        )
        result = NetworkBlockingChecker._assessment("203.0.113.10", local, (), chebur)
        self.assertEqual(result.tspu_probability, 35)
        self.assertNotEqual(result.tspu_probability, 100)


if __name__ == "__main__":
    unittest.main()
