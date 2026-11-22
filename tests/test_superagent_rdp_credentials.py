"""Windows RDP credentials are separate from the SSH login parser."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from leshy.superagent_rdp_credentials import (
    parse_rdp_credential_document, load_owner_rdp_credentials,
)

HOST = "192.0.2.10"


def payload(**changes):
    obj = {"host": HOST, "username": "user", "password": "secret"}
    obj.update(changes)
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def test_basic_ssh_style_json_is_backward_compatible_for_rdp():
    creds = parse_rdp_credential_document(payload(), HOST)
    assert (creds.username, creds.password, creds.domain) == ("user", "secret", "")


def test_windows_domain_backslash_username_is_split():
    creds = parse_rdp_credential_document(payload(username="CORP\\operator"), HOST)
    assert creds.domain == "CORP" and creds.username == "operator"


def test_explicit_domain_and_unicode_upn_supported():
    one = parse_rdp_credential_document(payload(domain="CORP", username="Леший"), HOST)
    two = parse_rdp_credential_document(payload(username="user@corp.example"), HOST)
    assert one.domain == "CORP" and one.username == "Леший"
    assert two.username == "user@corp.example" and two.domain == ""


def test_dot_domain_for_local_windows_account():
    creds = parse_rdp_credential_document(payload(username=".\\localuser"), HOST)
    assert creds.domain == "." and creds.username == "localuser"


@pytest.mark.parametrize("raw", [
    b"",
    b"not json",
    b"{" + b" " * 8192 + b"}",
    b'{"host":"192.0.2.10","host":"192.0.2.10","username":"x","password":"y"}',
    payload(host="192.0.2.11"),
    payload(username="../admin"),
    payload(username="DOMAIN\\user", domain="OTHER"),
    payload(username="DOMAIN\\user\\wrong"),
    payload(password="secret\ninsecure"),
    payload(username=""),
    payload(password=""),
    payload(other="unexpected"),
    payload(username=True),
    payload(domain="UPN@invalid"),
    payload(username="a" * 129),
])
def test_invalid_files_fail_closed(raw):
    with pytest.raises((ValueError, PermissionError)):
        parse_rdp_credential_document(raw, HOST)


def test_secret_reader_uses_exact_owner_file_without_logging(tmp_path, monkeypatch):
    import leshy.superagent_rdp_credentials as module
    path = tmp_path / "rdp.json"
    path.write_bytes(payload(username="CORP\\admin"))
    monkeypatch.setattr(module, "find_credential_file", lambda storage, owner, filename: path)
    result = load_owner_rdp_credentials(object(), "owner", HOST, "rdp.json")
    assert result.username == "admin" and result.domain == "CORP"
    assert "secret" not in repr(result)


def test_symlink_source_is_rejected(tmp_path, monkeypatch):
    import leshy.superagent_rdp_credentials as module
    path = tmp_path / "rdp.json"
    target = tmp_path / "target.json"
    target.write_bytes(payload())
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not permitted on this Windows host")
    monkeypatch.setattr(module, "find_credential_file", lambda *a: path)
    with pytest.raises((PermissionError, OSError)):
        load_owner_rdp_credentials(object(), "owner", HOST, "rdp.json")


def test_oversized_source_is_rejected_before_json_parse(tmp_path, monkeypatch):
    import leshy.superagent_rdp_credentials as module
    path = tmp_path / "large.json"
    path.write_bytes(b" " * 9000)
    monkeypatch.setattr(module, "find_credential_file", lambda *a: path)
    with pytest.raises(PermissionError):
        load_owner_rdp_credentials(object(), "owner", HOST, "large.json")



def test_numeric_json_ip_is_not_a_literal_host():
    # ipaddress.ip_address accepts an integer, but owner JSON must use an
    # explicit string to prevent type ambiguity and accidental aliasing.
    with pytest.raises(PermissionError):
        parse_rdp_credential_document(payload(host=3221225994), HOST)


@pytest.mark.parametrize("field", ["username", "password", "domain"])
def test_lone_surrogate_does_not_reach_credssp(field):
    doc = {"host": HOST, "username": "user", "password": "secret"}
    doc[field] = "\ud800"
    # A lone surrogate must arrive as a JSON escape, not be rejected by
    # the UTF-8 test fixture encoder before the parser is invoked.
    raw = json.dumps(doc, ensure_ascii=True).encode("ascii")
    with pytest.raises(ValueError):
        parse_rdp_credential_document(raw, HOST)


def test_host_rejects_bool_json_value_even_when_pinned_node_is_known():
    with pytest.raises(PermissionError):
        parse_rdp_credential_document(payload(host=True), HOST)



def test_modified_file_during_read_is_rejected(tmp_path, monkeypatch):
    import os
    import leshy.superagent_rdp_credentials as module
    path = tmp_path / "owner.json"
    path.write_bytes(payload())
    monkeypatch.setattr(module, "find_credential_file", lambda *_: path)
    read = module.os.read
    happened = []

    def mutate_after_first_read(fd, count):
        value = read(fd, count)
        if value and not happened:
            happened.append(True)
            path.write_bytes(payload(password="other-secret"))
        return value

    monkeypatch.setattr(module.os, "read", mutate_after_first_read)
    with pytest.raises(PermissionError, match="changed during read"):
        module.load_owner_rdp_credentials(object(), "owner", HOST, "owner.json")
    assert happened


def test_replaced_file_during_read_is_rejected(tmp_path, monkeypatch):
    import os
    import leshy.superagent_rdp_credentials as module
    path = tmp_path / "owner.json"
    path.write_bytes(payload())
    alternate = tmp_path / "replacement.json"
    alternate.write_bytes(payload(password="a-changed-secret"))
    monkeypatch.setattr(module, "find_credential_file", lambda *_: path)
    read = module.os.read
    happened = []

    def swap_entry_during_read(fd, count):
        value = read(fd, count)
        if value and not happened:
            happened.append(True)
            os.replace(alternate, path)
        return value

    monkeypatch.setattr(module.os, "read", swap_entry_during_read)
    with pytest.raises(PermissionError, match="changed during read"):
        module.load_owner_rdp_credentials(object(), "owner", HOST, "owner.json")
    assert happened


def test_file_size_grows_during_read_and_never_returns_secrets(tmp_path, monkeypatch):
    import leshy.superagent_rdp_credentials as module
    path = tmp_path / "owner.json"
    path.write_bytes(payload())
    monkeypatch.setattr(module, "find_credential_file", lambda *_: path)
    read = module.os.read
    def extend_file(fd, count):
        value = read(fd, count)
        with path.open("ab") as output:
            output.write(b" " * 128)
        return value
    monkeypatch.setattr(module.os, "read", extend_file)
    with pytest.raises(PermissionError):
        module.load_owner_rdp_credentials(object(), "owner", HOST, "owner.json")
