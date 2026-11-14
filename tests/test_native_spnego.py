"""OS-native SPNEGO security boundary tests; no external RDP host needed."""
from __future__ import annotations

import ctypes as C
import os
import pytest

from leshy.native_spnego import SPNEGOError, UnixGSSContext, _check_target, _check_token
from leshy.native_spnego_windows import SecHandle, SecBuffer, WindowsSSPIContext


@pytest.mark.parametrize("host", ("server.example.com", "rdp.internal.local", "host.domain"))
def test_valid_dns_service(host):
    assert _check_target(host) == host


@pytest.mark.parametrize("host", (
    "127.0.0.1", "host", "http://foo.example", "foo..example", "-bad.example",
    "host-.example", "foo/example.com", "foo.example.com:3389", "", "x" * 254 + ".com",
))
def test_invalid_or_unsafe_service(host):
    with pytest.raises(ValueError):
        _check_target(host)


def test_sspi_abi_sizes_and_platform_isolation():
    width = C.sizeof(C.c_void_p)
    assert C.sizeof(SecHandle) == width * 2
    assert C.sizeof(SecBuffer) == (16 if width == 8 else 12)
    if os.name != "nt":
        with pytest.raises(SPNEGOError):
            WindowsSSPIContext("server.example.com", authorize=lambda: None)


def test_authorization_required_before_gssapi_load():
    def deny():
        raise PermissionError("not owner")

    with pytest.raises(PermissionError, match="not owner"):
        UnixGSSContext("server.example.com", authorize=deny)


def test_native_gssapi_fails_closed_without_valid_credentials():
    if os.name != "posix":
        pytest.skip("POSIX only")
    try:
        engine = UnixGSSContext("noticket.example.invalid", authorize=lambda: None)
    except SPNEGOError:
        pytest.skip("system GSSAPI is not installed")
    try:
        try:
            first = engine.step()
        except SPNEGOError:
            assert not engine.complete
        else:
            # If OS has a ticket, don't forward the initial token anywhere.
            assert isinstance(first, bytes)
    finally:
        engine.close()
        engine.close()


def test_token_limits():
    with pytest.raises(ValueError):
        _check_token(b"x" * (128 * 1024 + 1))
    with pytest.raises(ValueError):
        _check_token("text")
    with pytest.raises(ValueError):
        _check_token(b"")
