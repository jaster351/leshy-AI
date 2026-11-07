"""Checks that both requested TCP ports are reported regardless of OS inference."""
import unittest

from leshy.network_blocking_module import (
    CheburcheckEvidence,
    NetworkBlockingChecker,
    TcpObservation,
)


def assess(local, remote=()):
    return NetworkBlockingChecker._assessment(
        "1.1.1.1", tuple(local), tuple(remote),
        CheburcheckEvidence(ok=False, error="Cheburcheck offline"),
    )


class PortVisibilityTests(unittest.TestCase):
    def test_two_refused_ports_do_not_guess_os(self):
        report = assess((
            TcpObservation("local", 22, "refused"),
            TcpObservation("local", 3389, "refused"),
        ))
        self.assertEqual(report.profile, "Не определён")
        hint, status = NetworkBlockingChecker._os_hypothesis(report.local, report.remote)
        self.assertEqual(status, "unknown")
        self.assertIn("нет подтверждения", hint)
        formatted = NetworkBlockingChecker.format(report)
        self.assertIn("TCP/22 (SSH/Linux): refused", formatted)
        self.assertIn("TCP/3389 (RDP/Windows): refused", formatted)
        self.assertIn("Check-Host TCP/22 (SSH): нет результатов", formatted)
        self.assertIn("Check-Host TCP/3389 (RDP): нет результатов", formatted)

    def test_foreign_ssh_connection_is_flagged_as_possible_linux_not_fact(self):
        local = (
            TcpObservation("local", 22, "timeout"),
            TcpObservation("local", 3389, "refused"),
        )
        remote = (
            TcpObservation("check-host", 22, "connected", country_code="de", node="de1"),
            TcpObservation("check-host", 3389, "refused", country_code="de", node="de1"),
        )
        report = assess(local, remote)
        hint, status = NetworkBlockingChecker._os_hypothesis(report.local, report.remote)
        self.assertEqual(status, "possible_linux_unix")
        self.assertIn("Linux/Unix", hint)
        self.assertIn("Windows", hint)
        self.assertIn("Check-Host TCP/22: DE: connected", NetworkBlockingChecker.format(report))
        self.assertIn("Локально TCP/22 (SSH/Linux): timeout", NetworkBlockingChecker.format(report))

    def test_rdp_connection_is_windows_hypothesis_only(self):
        local = (
            TcpObservation("local", 22, "timeout"),
            TcpObservation("local", 3389, "connected"),
        )
        report = assess(local)
        hint, status = NetworkBlockingChecker._os_hypothesis(report.local, report.remote)
        self.assertEqual(status, "possible_windows")
        self.assertIn("Windows", hint)
        self.assertIn("Linux", hint)
        self.assertEqual(report.service_ports, (3389,))
        ports = NetworkBlockingChecker._port_results(report)
        self.assertFalse(ports[22]["any_connected"])
        self.assertTrue(ports[3389]["any_connected"])
        self.assertEqual(ports[22]["local_status"], "timeout")
        self.assertEqual(ports[3389]["local_status"], "connected")

    def test_both_connected_do_not_establish_os(self):
        report = assess((
            TcpObservation("local", 22, "connected"),
            TcpObservation("local", 3389, "connected"),
        ))
        hint, status = NetworkBlockingChecker._os_hypothesis(report.local, report.remote)
        self.assertEqual(status, "ambiguous")
        self.assertIn("ОС не подтверждена", hint)


if __name__ == "__main__":
    unittest.main()
