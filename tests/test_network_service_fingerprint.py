"""Offline regression checks for OS hints and stage order (no real network calls)."""
import unittest
from unittest.mock import patch

from leshy.network_service_fingerprint import _classify
from leshy.network_blocking_module import (
    CheburProbeSummary,
    CheburcheckEvidence,
    NetworkBlockingChecker,
    TcpObservation,
)


def row(port, status="connected", banner=""):
    return {"port": port, "service": "TCP", "status": status,
            "detail": "", "latency_ms": 1, "banner": banner}


class OsFingerprintTests(unittest.TestCase):
    def test_explicit_windows_openssh_banner(self):
        family, confidence, evidence = _classify(
            [row(22, banner="SSH-2.0-OpenSSH_for_Windows_9.5")]
        )
        self.assertEqual(family, "Windows")
        self.assertEqual(confidence, "средняя")
        self.assertIn("TCP/22", evidence[0])

    def test_debian_ssh_banner_is_a_hint_not_proof(self):
        family, confidence, _ = _classify(
            [row(22, banner="SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3")]
        )
        self.assertEqual(family, "Linux/Unix")
        self.assertEqual(confidence, "средняя")

    def test_rdp_or_smb_alone_has_low_confidence(self):
        family, confidence, evidence = _classify([row(3389), row(445)])
        self.assertEqual(family, "возможно Windows")
        self.assertEqual(confidence, "низкая")
        self.assertIn("Linux", evidence[0])

    def test_no_open_ports_means_os_unknown(self):
        family, confidence, _ = _classify(
            [row(22, "timeout"), row(3389, "refused")]
        )
        self.assertEqual(family, "не определена")
        self.assertEqual(confidence, "отсутствует")

    def test_conflicting_server_signatures_are_ambiguous(self):
        family, confidence, _ = _classify(
            [row(22, banner="SSH-2.0-OpenSSH_for_Windows_9.5"),
             row(80, banner="Apache/2.4.62 (Ubuntu)")]
        )
        self.assertEqual(family, "неоднозначна")
        self.assertEqual(confidence, "низкая")

    def test_fingerprint_runs_before_external_services(self):
        checker = NetworkBlockingChecker()
        calls = []
        local = (
            TcpObservation("local", 22, "refused"),
            TcpObservation("local", 3389, "refused"),
        )
        fingerprint = {
            "os_family": "не определена", "confidence": "отсутствует",
            "evidence": ["Нет достоверных баннеров"], "completed": True,
            "ports": [
                row(22, "refused"), row(80, "connected"),
                row(443, "timeout"), row(445, "refused"),
                row(3389, "refused"),
            ],
        }

        def local_check(_target):
            calls.append("local")
            return local

        def collect(_target, _ports, **_kwargs):
            calls.append("fingerprint")
            return fingerprint

        def remote(_target):
            self.assertEqual(calls[:2], ["local", "fingerprint"])
            return (
                TcpObservation("check-host", 443, "timeout", country_code="ru", node="ru1"),
                TcpObservation("check-host", 443, "connected", country_code="de", node="de1"),
            )

        with (
            patch.object(checker, "_check_local_ports", side_effect=local_check),
            patch("leshy.network_blocking_module.collect_service_fingerprint", side_effect=collect),
            patch.object(checker, "_check_host", side_effect=remote),
            patch.object(checker, "_cheburcheck", return_value=CheburcheckEvidence(ok=False)),
            patch.object(checker, "_chebur_probe", return_value=CheburProbeSummary(ok=False)),
        ):
            result = checker.check("1.1.1.1")
        self.assertEqual(result.os_fingerprint["confidence"], "отсутствует")
        self.assertIn(443, [item.port for item in result.local])
        self.assertIn("TCP/443", checker.format(result))
        self.assertEqual(len(result.tool_receipts), 3)


if __name__ == "__main__":
    unittest.main()
