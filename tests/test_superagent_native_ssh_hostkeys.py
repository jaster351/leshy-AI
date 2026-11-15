"""Offline checks for untrusted OpenSSH host-key discovery output."""
from __future__ import annotations

import base64
import hashlib
import unittest

from leshy.superagent_native_ssh_hostkeys import pinned_known_hosts


class HostKeyDiscoveryTests(unittest.TestCase):
    def setUp(self):
        algorithm = b"ssh-ed25519"
        self.key = len(algorithm).to_bytes(4, "big") + algorithm + b"x" * 32
        self.pin = hashlib.sha256(self.key).hexdigest()
        self.line = b"192.0.2.8 ssh-ed25519 " + base64.b64encode(self.key)

    def test_exact_enrolled_key_accepted(self):
        self.assertEqual(
            pinned_known_hosts("192.0.2.8", self.line + b"\n", self.pin),
            self.line + b"\n",
        )

    def test_wrong_address_rejected_even_when_key_matches(self):
        with self.assertRaises(PermissionError):
            pinned_known_hosts(
                "192.0.2.9", self.line + b"\n", self.pin,
            )

    def test_wrong_algorithm_rejected(self):
        with self.assertRaises(PermissionError):
            pinned_known_hosts(
                "192.0.2.8", self.line.replace(b"ssh-ed25519 ", b"ssh-rsa ", 1), self.pin,
            )

    def test_wrong_pin_rejected(self):
        with self.assertRaises(PermissionError):
            pinned_known_hosts("192.0.2.8", self.line, "0" * 64)

    def test_oversized_discovery_rejected(self):
        with self.assertRaises(PermissionError):
            pinned_known_hosts("192.0.2.8", b"x" * 65537, self.pin)

    def test_no_untrusted_host_wildcard(self):
        with self.assertRaises(PermissionError):
            pinned_known_hosts(
                "192.0.2.8", self.line.replace(b"192.0.2.8", b"*"), self.pin,
            )


if __name__ == "__main__":
    unittest.main()
