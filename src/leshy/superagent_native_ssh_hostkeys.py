"""Strict, bounded parsing of untrusted ssh-keyscan output.

A discovered key never grants trust: its SHA-256 must already be enrolled.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress


def pinned_known_hosts(host: str, scan: bytes, fingerprint: str) -> bytes:
    """Return a single verified known_hosts entry or fail closed.

    Reject malformed lines, different hosts, certificates, key-type mismatch,
    oversized discovery output and ambiguous/unexpected key material.
    """
    address = str(ipaddress.ip_address(host))
    if not isinstance(scan, bytes) or len(scan) > 65536:
        raise PermissionError("Invalid SSH key discovery response")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise PermissionError("Invalid enrolled SSH fingerprint")
    try:
        bytes.fromhex(fingerprint)
    except ValueError as exc:
        raise PermissionError("Invalid enrolled SSH fingerprint") from exc
    accepted_hosts = {address, f"[{address}]:22"}
    for line in scan.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0].startswith(b"#"):
            continue
        try:
            discovered_host = parts[0].decode("ascii")
            algorithm = parts[1].decode("ascii")
            if discovered_host not in accepted_hosts:
                continue
            if algorithm not in {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256",
                                 "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521"}:
                continue
            key = base64.b64decode(parts[2], validate=True)
            # SSH wire public key starts with its length-prefixed algorithm.
            if len(key) < 8 or len(key) > 16384:
                continue
            size = int.from_bytes(key[:4], "big")
            if size != len(algorithm) or key[4:4 + size] != parts[1]:
                continue
            if hmac.compare_digest(hashlib.sha256(key).hexdigest(), fingerprint.lower()):
                return line + b"\n"
        except (UnicodeError, ValueError, binascii.Error):
            continue
    raise PermissionError("SSH host key differs from enrolled fingerprint")


__all__ = ["pinned_known_hosts"]
