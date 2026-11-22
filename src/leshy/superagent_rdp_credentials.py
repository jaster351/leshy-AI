"""Read one owner-chosen RDP JSON credential without SSH username restrictions.

No Web/LLM path can supply the password directly. This reader is used ONLY
after a scoped, live owner approval and the pinned TLS+CredSSP server binding.
It never logs file paths, usernames, domain names, passwords or raw JSON.
"""
from __future__ import annotations

import ipaddress
import json
import os
import stat
from typing import Any

from .rdp_credssp_credentials import RDPCredentials
from .superagent_credential_settings import find_credential_file

_MAX_BYTES = 8192
_MAX_USERNAME = 128
_MAX_PASSWORD = 2048
_MAX_DOMAIN = 128


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key in RDP credential JSON")
        result[key] = value
    return result


def parse_rdp_credential_document(raw: bytes, node_id: str) -> RDPCredentials:
    """Bounded UTF-8 JSON with optional domain or DOMAIN\\user login syntax."""
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_BYTES:
        raise ValueError("Invalid bounded RDP credential document")
    try:
        doc = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Invalid UTF-8 RDP credential JSON") from None
    if type(doc) is not dict or set(doc) not in (
        {"host", "username", "password"},
        {"host", "username", "password", "domain"},
    ):
        raise ValueError("Unexpected RDP credential keys")
    try:
        actual_host = str(ipaddress.ip_address(doc["host"]))
        expected_host = str(ipaddress.ip_address(node_id))
    except (ValueError, TypeError):
        raise PermissionError("Credential document does not have a literal host") from None
    if actual_host != expected_host:
        raise PermissionError("RDP credentials were configured for a different node")
    username, password = doc["username"], doc["password"]
    domain = doc.get("domain", "")
    if (type(username) is not str or type(password) is not str
            or type(domain) is not str):
        raise ValueError("RDP credential fields must be strings")
    if "\\" in username:
        if domain or username.count("\\") != 1:
            raise ValueError("Ambiguous Windows RDP domain")
        domain, username = username.split("\\", 1)
    for name, value, limit, optional in (
        ("username", username, _MAX_USERNAME, False),
        ("password", password, _MAX_PASSWORD, False),
        ("domain", domain, _MAX_DOMAIN, True),
    ):
        if (len(value) > limit or (not optional and not value)
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise ValueError("Invalid RDP credential " + name)
    if (username.strip() != username or "/" in username or "\\" in username
            or domain.strip() != domain or any(c in domain for c in "/\\:@")):
        raise ValueError("Invalid Windows RDP login fields")
    return RDPCredentials(username=username, password=password, domain=domain)


def load_owner_rdp_credentials(
    storage: Any, owner_id: str, node_id: str, filename: str,
) -> RDPCredentials:
    """Race-aware bounded read of the owner-scoped exact local JSON file."""
    path = find_credential_file(storage, owner_id, filename)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BYTES:
        raise PermissionError("RDP credential file must be a small regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        after = os.fstat(fd)
        if (not stat.S_ISREG(after.st_mode) or after.st_size > _MAX_BYTES
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            raise PermissionError("RDP credential file changed before open")
        chunks = bytearray()
        while len(chunks) <= _MAX_BYTES:
            part = os.read(fd, min(4096, _MAX_BYTES + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        if not chunks or len(chunks) > _MAX_BYTES:
            raise PermissionError("RDP credential file exceeds size policy")
    finally:
        os.close(fd)
    try:
        return parse_rdp_credential_document(bytes(chunks), node_id)
    finally:
        # Mutable buffer can be cleared; Python also holds immutable copies.
        chunks[:] = b"\0" * len(chunks)


__all__ = ["parse_rdp_credential_document", "load_owner_rdp_credentials"]
