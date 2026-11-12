"""Regression tests for conservative TCP/UDP remote OS checks."""
from unittest import TestCase
from unittest.mock import patch

from leshy import remote_os_fingerprint as f


class FingerprintTests(TestCase):
    def test_target_is_user_specified_single_literal(self):
        self.assertEqual(f.target_from_request("Обнови сервер 192.168.1.50"), "192.168.1.50")
        self.assertIsNone(f.target_from_request("Сравни серверы 192.168.1.50 и 192.168.1.51"))
        self.assertIsNone(f.target_from_request("Текст 192.168.1.50"))
        for value in ("127.0.0.1", "0.0.0.0", "224.0.0.1", "169.254.2.1",
                      "192.168.1.0/24", "hostname.com"):
            with self.assertRaises(ValueError):
                f.validate_target(value)

    def test_unknown_does_not_guess_from_ssh_smb_or_udp(self):
        def tcp(host, port, timeout):
            return ("open", "") if port in (22, 445) else ("closed", "")
        with patch.object(f, "_tcp", side_effect=tcp), patch.object(f, "_udp", return_value="responsive"):
            result = f.probe_remote_os("192.168.1.50")
        self.assertEqual(result["os_family"], "unknown")
        self.assertFalse(result["verified"])
        self.assertEqual(len(result["tcp"]), 8)
        self.assertEqual(len(result["udp"]), 2)

    def test_linux_banner_is_only_inferred_not_verified(self):
        def tcp(host, port, timeout):
            return ("open", "ssh_linux") if port == 22 else ("unknown", "")
        with patch.object(f, "_tcp", side_effect=tcp), patch.object(f, "_udp", return_value="no_response"):
            result = f.probe_remote_os("10.4.5.6")
        self.assertEqual(result["os_family"], "Linux")
        self.assertEqual(result["confidence"], "medium")
        self.assertFalse(result["verified"])

    def test_winrm_is_only_inferred_not_verified(self):
        def tcp(host, port, timeout):
            return ("open", "") if port in (5985, 3389, 445) else ("unknown", "")
        with patch.object(f, "_tcp", side_effect=tcp), patch.object(f, "_udp", return_value="no_response"):
            result = f.probe_remote_os("10.4.5.6")
        self.assertEqual(result["os_family"], "Windows")
        self.assertFalse(result["verified"])

    def test_valid_dns_and_netbios_packets(self):
        self.assertIn(b"example", f._udp_query(53, b"\x12\x34"))
        self.assertEqual(len(f._udp_query(137, b"\x12\x34")), 50)

    def test_remote_diagnostics_uses_explicit_permission_switch(self):
        from leshy.module_settings import MODULE_SETTINGS
        definition = next(item for item in MODULE_SETTINGS if item.name == "remote-admin")
        self.assertFalse(definition.default_enabled)
        self.assertIn("os-control", definition.dependencies)
