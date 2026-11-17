"""Fail-closed regression cases for RDP X.224 preflight; no network required."""
import struct
import unittest
from unittest.mock import Mock

from leshy.rdp_tls_probe import _negotiate, probe_rdp_tls


class X224PreflightNegativeTests(unittest.TestCase):
    def _socket(self, body):
        packet = b"\x03\x00" + struct.pack(">H", len(body) + 4) + body
        sock = Mock()
        pieces = [packet[:4], packet[4:]]
        sock.recv.side_effect = lambda n: pieces.pop(0)
        return sock

    def test_reject_legacy_security_downgrade(self):
        body = b"\x0e\xd0\x00\x00\x00\x00\x00" + struct.pack("<BBHI", 2, 0, 8, 0)
        with self.assertRaises(ConnectionError):
            _negotiate(self._socket(body))

    def test_reject_unexpected_security_selection(self):
        body = b"\x0e\xd0\x00\x00\x00\x00\x00" + struct.pack("<BBHI", 2, 0, 8, 4)
        with self.assertRaises(ConnectionError):
            _negotiate(self._socket(body))

    def test_reject_malformed_negotiation_length(self):
        body = b"\x0e\xd0\x00\x00\x00\x00\x00" + struct.pack("<BBHI", 2, 0, 7, 2)
        with self.assertRaises(ConnectionError):
            _negotiate(self._socket(body))

    def test_reject_missing_tls_pin_before_connect(self):
        with self.assertRaises(PermissionError):
            probe_rdp_tls("127.0.0.1", "")

    def test_reject_hostname_target_before_connect(self):
        with self.assertRaises(ValueError):
            probe_rdp_tls("example.invalid", "0" * 64)


if __name__ == "__main__":
    unittest.main()
