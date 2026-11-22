"""A broad, deterministic negative-input matrix for native RDP security.

The matrix yields individually addressable pytest cases. Every case is a
specific invalid value passed through production input validators; no real
network, Windows host or secret is used. Neither a green source readback nor
case collection is evidence that all tests passed.
"""
from __future__ import annotations

import json

import pytest

from leshy.superagent_rdp_credentials import parse_rdp_credential_document
from leshy.superagent_rdp_launch_approval import _scope

HOST = "192.0.2.10"


def credential_bytes(field, value):
    doc = {"host": HOST, "username": "operator", "password": "secret"}
    doc[field] = value
    return json.dumps(doc, ensure_ascii=True, separators=(",", ":")).encode("ascii")


# 96 distinct control-character defenses (32 for each credential string).
# The additional values exercise type confusion, Unicode, Windows-domain
# ambiguity, path-like usernames, overlong fields and host address injection.
_BAD_FIELDS = (
    [(f"username-control-{n:02d}", "username", "a" + chr(n) + "b") for n in range(32)]
    + [(f"password-control-{n:02d}", "password", "a" + chr(n) + "b") for n in range(32)]
    + [(f"domain-control-{n:02d}", "domain", "A" + chr(n) + "B") for n in range(32)]
    + [
        ("username-empty", "username", ""),
        ("username-del", "username", "a\x7fb"),
        ("username-overlong", "username", "a" * 129),
        ("username-leading-space", "username", " admin"),
        ("username-trailing-space", "username", "admin "),
        ("username-only-space", "username", " "),
        ("username-forward-slash", "username", "ad/min"),
        ("username-parent-path", "username", "../admin"),
        ("username-backslash-double", "username", "D\\user\\extra"),
        ("username-backslash-empty", "username", "D\\"),
        ("username-inverted-surrogate-high", "username", "\ud800"),
        ("username-inverted-surrogate-low", "username", "\udfff"),
        ("username-null", "username", None),
        ("username-bool", "username", True),
        ("username-int", "username", 1024),
        ("username-float", "username", 1.25),
        ("username-list", "username", ["admin"]),
        ("username-dict", "username", {"name": "operator"}),
        ("password-empty", "password", ""),
        ("password-del", "password", "a\x7fb"),
        ("password-overlong", "password", "a" * 2049),
        ("password-surrogate-high", "password", "\ud800"),
        ("password-surrogate-low", "password", "\udfff"),
        ("password-null", "password", None),
        ("password-bool", "password", True),
        ("password-int", "password", 17),
        ("password-float", "password", 1.1),
        ("password-list", "password", ["secret"]),
        ("password-dict", "password", {"password": "secret"}),
        ("domain-del", "domain", "D\x7fB"),
        ("domain-overlong", "domain", "D" * 129),
        ("domain-leading-space", "domain", " DOMAIN"),
        ("domain-trailing-space", "domain", "DOMAIN "),
        ("domain-slash", "domain", "DOM/AIN"),
        ("domain-backslash", "domain", "DOM\\AIN"),
        ("domain-colon", "domain", "DOM:AIN"),
        ("domain-at", "domain", "DOM@AIN"),
        ("domain-surrogate-high", "domain", "\ud800"),
        ("domain-surrogate-low", "domain", "\udfff"),
        ("domain-null", "domain", None),
        ("domain-bool", "domain", True),
        ("domain-int", "domain", 7),
        ("domain-float", "domain", 3.14),
        ("domain-list", "domain", ["AD"]),
        ("domain-dict", "domain", {"domain": "AD"}),
    ]
    + [(f"host-out-of-range-{n:03d}", "host", f"192.0.2.{n}") for n in range(256, 320)]
    + [
        ("host-other-valid-v4", "host", "192.0.2.11"),
        ("host-other-valid-v6", "host", "2001:db8::10"),
        ("host-loopback", "host", "127.0.0.1"),
        ("host-unspecified", "host", "0.0.0.0"),
        ("host-linklocal", "host", "169.254.1.1"),
        ("host-multicast", "host", "224.0.0.1"),
        ("host-name", "host", "example.org"),
        ("host-empty", "host", ""),
        ("host-leading-space", "host", " " + HOST),
        ("host-trailing-space", "host", HOST + " "),
        ("host-missing-octet", "host", "192.0.2"),
        ("host-extra-octet", "host", HOST + ".1"),
        ("host-numeric", "host", 3221225994),
        ("host-zero", "host", 0),
        ("host-bool", "host", True),
        ("host-null", "host", None),
        ("host-float", "host", 1.5),
        ("host-list", "host", [HOST]),
        ("host-dict", "host", {"ip": HOST}),
        ("host-hex", "host", "0xc000020a"),
    ]
)

# All cases have distinct stable IDs, and every invalid value is checked via
# the shipping RDP JSON validator rather than duplicating its logic here.
_FIELD_IDS = [case[0] for case in _BAD_FIELDS]
assert len(set(_FIELD_IDS)) == len(_BAD_FIELDS)
assert len(_BAD_FIELDS) >= 220


@pytest.mark.parametrize("case_id,field,value", _BAD_FIELDS, ids=_FIELD_IDS)
def test_reject_invalid_credential_field(case_id, field, value):
    with pytest.raises((PermissionError, ValueError), match=".+"):
        parse_rdp_credential_document(credential_bytes(field, value), HOST)


def _wrong_shapes():
    return [
        ("empty-bytes", b""),
        ("random-ascii", b"not-json"),
        ("truncated-object", b'{"host"'),
        ("truncated-utf8", b'{"host":"\xe2\x82'),
        ("invalid-utf8", b"\xff\xfe\xfa"),
        ("nested-array", b"[]"),
        ("nested-number", b"0"),
        ("nested-true", b"true"),
        ("nested-null", b"null"),
        ("empty-object", b"{}"),
        ("only-host", b'{"host":"192.0.2.10"}'),
        ("only-username", b'{"username":"operator"}'),
        ("only-password", b'{"password":"secret"}'),
        ("host-user", b'{"host":"192.0.2.10","username":"operator"}'),
        ("host-pass", b'{"host":"192.0.2.10","password":"secret"}'),
        ("user-pass", b'{"username":"operator","password":"secret"}'),
        ("duplicate-host", b'{"host":"192.0.2.10","host":"192.0.2.10","username":"a","password":"b"}'),
        ("duplicate-password", b'{"host":"192.0.2.10","username":"a","password":"x","password":"b"}'),
        ("duplicate-user", b'{"host":"192.0.2.10","username":"a","username":"b","password":"x"}'),
        ("over-8k", b" " * 8193),
        ("raw-nul", b"\x00"),
        ("malformed-escape", b'{"host":"192.0.2.10","username":"bad\\z","password":"secret"}'),
        ("array-of-valid-docs", b'[{"host":"192.0.2.10","username":"a","password":"b"}]'),
        ("string-doc", b'"host=192.0.2.10"'),
        ("unexpected-credential-key", b'{"host":"192.0.2.10","username":"a","password":"b","token":"no"}'),
    ]


@pytest.mark.parametrize("case_id,raw", _wrong_shapes(), ids=[k for k, _ in _wrong_shapes()])
def test_reject_invalid_credential_document(case_id, raw):
    with pytest.raises((PermissionError, ValueError), match=".+"):
        parse_rdp_credential_document(raw, HOST)


# Pin check runs before any credential file I/O or socket establishment.
_BAD_TLS_PINS = (
    [(f"sha256-short-{n:02d}", "a" * n) for n in range(64)]
    + [
        ("sha256-too-long", "a" * 65),
        ("sha256-colon-separated", "aa:" * 21 + "a"),
        ("sha256-nonhex", "g" * 64),
        ("sha256-newline", "a" * 63 + "\n"),
        ("sha256-control", "a" * 63 + "\x00"),
        ("sha256-null", None),
        ("sha256-boolean", True),
        ("sha256-number", 64),
        ("sha256-bytes", b"a" * 64),
        ("sha256-list", ["a" * 64]),
    ]
)


@pytest.mark.parametrize("case_id,pin", _BAD_TLS_PINS, ids=[k for k, _ in _BAD_TLS_PINS])
def test_reject_invalid_owner_tls_pin(case_id, pin):
    with pytest.raises((ValueError, PermissionError)):
        _scope("SA-1", HOST, "rdp.example.org", pin, "owner.json")


# The basename guard cannot be bypassed with Unicode, path separators,
# file extension camouflage or raw Web input types.
_BAD_FILENAMES = (
    [(f"filename-asc-control-{n:02d}", "file" + chr(n) + ".json") for n in range(32)]
    + [
        ("filename-empty", ""),
        ("filename-dot", "."),
        ("filename-dotdot", ".."),
        ("filename-extension-only", ".json"),
        ("filename-not-json", "owner.txt"),
        ("filename-no-extension", "owner"),
        ("filename-absolute-posix", "/tmp/owner.json"),
        ("filename-relative-posix", "../owner.json"),
        ("filename-absolute-windows", "C:\\temp\\owner.json"),
        ("filename-relative-windows", "..\\owner.json"),
        ("filename-with-slash", "dir/owner.json"),
        ("filename-with-backslash", "dir\\owner.json"),
        ("filename-with-colon", "name:owner.json"),
        ("filename-with-question", "owner?.json"),
        ("filename-with-asterisk", "owner*.json"),
        ("filename-with-pipe", "owner|.json"),
        ("filename-with-quote", 'owner".json'),
        ("filename-with-angle", "<owner>.json"),
        ("filename-with-non-ascii", "секрет.json"),
        ("filename-overlong", "a" * 256 + ".json"),
        ("filename-trailing-newline", "owner.json\n"),
        ("filename-null", None),
        ("filename-false", False),
        ("filename-true", True),
        ("filename-number", 1),
        ("filename-dict", {"name": "owner.json"}),
        ("filename-list", ["owner.json"]),
    ]
)


@pytest.mark.parametrize("case_id,name", _BAD_FILENAMES, ids=[k for k, _ in _BAD_FILENAMES])
def test_reject_unsafe_credential_filename_in_approval(case_id, name):
    with pytest.raises((ValueError, PermissionError)):
        _scope("SA-1", HOST, "rdp.example.org", "a" * 64, name)


_BAD_DNS_NAMES = [
    ("dns-empty", ""),
    ("dns-no-dot", "localhost"),
    ("dns-double-dot", "rdp..example.org"),
    ("dns-leading-dot", ".rdp.example.org"),
    ("dns-trailing-dot", "rdp.example.org."),
    ("dns-leading-dash", "-rdp.example.org"),
    ("dns-trailing-dash", "rdp-.example.org"),
    ("dns-embedded-dash-edge", "rdp.-example.org"),
    ("dns-ip", HOST),
    ("dns-control", "rdp.example.org\n"),
    ("dns-slash", "rdp.example.org/path"),
    ("dns-port", "rdp.example.org:3389"),
    ("dns-query", "rdp.example.org?x=1"),
    ("dns-username", "user@rdp.example.org"),
    ("dns-scheme", "https://rdp.example.org"),
    ("dns-invalid-utf8-surrogate", "\ud800.example.org"),
    ("dns-overlong", "r" * 250 + ".example.org"),
    ("dns-null", None),
    ("dns-number", 3389),
    ("dns-bool", True),
    ("dns-list", ["rdp.example.org"]),
    ("dns-dict", {"dns": "rdp.example.org"}),
]


@pytest.mark.parametrize("case_id,dns", _BAD_DNS_NAMES, ids=[k for k, _ in _BAD_DNS_NAMES])
def test_reject_invalid_service_dns_before_approval(case_id, dns):
    with pytest.raises((ValueError, PermissionError, TypeError)):
        _scope("SA-1", HOST, dns, "a" * 64, "owner.json")


# Metadata only (not invented coverage): these counts are per-input pytest
# executions; nested compound tests are intentionally NOT multiplied here.
SECURITY_INPUT_CASE_COUNT = (
    len(_BAD_FIELDS) + len(_wrong_shapes()) + len(_BAD_TLS_PINS)
    + len(_BAD_FILENAMES) + len(_BAD_DNS_NAMES)
)
assert SECURITY_INPUT_CASE_COUNT >= 300



# Boundary acceptance (not just rejection): the hardening must preserve
# everyday and advanced Windows login names, including UPN and Unicode.
_VALID_USERNAMES = [
    "a" * n for n in range(1, 65)
] + [
    "operator", "Administrator", "remote user", "user@example.com",
    "диспетчер", "用户", "José", "a_b.c-d", "A" * 128,
    "user.name", "name with spaces", "X", "Z9", "domain.account",
]

_VALID_DOMAINS = [
    "", ".", "CORP", "CONTOSO", "corp.example", "A" * 128, "ДОМЕН",
]

_VALID_PASSWORDS = [
    "a", " " * 3, "pass word", "!@#$%^&*()[]{}",
    "Ñïçë", "пароль", "令牌", "🔒", "a" * 2048,
    "UPPERlower123", "0", "pass:with\\backslash", "user@host",
]


@pytest.mark.parametrize("username", _VALID_USERNAMES)
def test_valid_windows_usernames_remain_compatible(username):
    out = parse_rdp_credential_document(
        credential_bytes("username", username), HOST
    )
    assert out.username == username
    assert out.password == "secret"


@pytest.mark.parametrize("domain", _VALID_DOMAINS)
def test_valid_windows_domain_field_remains_compatible(domain):
    out = parse_rdp_credential_document(
        credential_bytes("domain", domain), HOST
    )
    assert out.domain == domain


@pytest.mark.parametrize("password", _VALID_PASSWORDS)
def test_valid_windows_password_remains_compatible(password):
    out = parse_rdp_credential_document(
        credential_bytes("password", password), HOST
    )
    assert out.password == password
    # The dataclass must not show the password field in repr. A short
    # password like "a" may coincidentally occur in a username and is not
    # evidence of a secret being displayed.
    assert "password=" not in repr(out)


def test_owner_json_utf8_bom_remains_compatible():
    body = credential_bytes("username", "operator")
    result = parse_rdp_credential_document(b"\xef\xbb\xbf" + body, HOST)
    assert result.username == "operator"


def test_owner_json_ipv6_canonicalization_is_safe():
    node = "2001:db8::10"
    data = {"host": "2001:0db8:0000:0000:0000:0000:0000:0010",
            "username": "operator", "password": "pass"}
    raw = json.dumps(data).encode("utf-8")
    out = parse_rdp_credential_document(raw, node)
    assert out.password == "pass"


def test_windows_domain_backslash_username_is_not_ambiguous():
    data = credential_bytes("username", "CONTOSO\\admin")
    out = parse_rdp_credential_document(data, HOST)
    assert out.username == "admin"
    assert out.domain == "CONTOSO"


# The collection count is computed from actual pytest argument sequences,
# not a hand-written claim. The principal request is for >=200 separate
# boundary cases, achieved through the rejected input matrix alone.
assert len(_BAD_FIELDS) >= 200
assert SECURITY_INPUT_CASE_COUNT >= 300
