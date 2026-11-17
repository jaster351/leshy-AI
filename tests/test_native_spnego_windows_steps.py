"""Mock the native SSPI ABI to exercise Negotiate completion statuses.

These are deterministic unit tests, not evidence of a real Windows RDP login.
"""
from __future__ import annotations

import ctypes as C
from types import SimpleNamespace

import pytest

import leshy.native_spnego_windows as win
from leshy.native_spnego import SPNEGOError


def fake_context(status, *, completion_status=0, invalid_pointer=False, bad_type=False):
    context = win.WindowsSSPIContext.__new__(win.WindowsSSPIContext)
    context.host = "server.example.com"
    context._cred, context._ctx, context._expiry = (
        win.SecHandle(), win.SecHandle(), win.TimeStamp()
    )
    context._have_cred = context._have_ctx = False
    context._closed = context._complete = False
    context._rounds = 0
    context._reauthorize = lambda: None
    context.close = lambda: setattr(context, "_closed", True)
    calls = []

    def initialize(*args):
        calls.append("initialize")
        assert args[2] == "TERMSRV/server.example.com"
        output = C.cast(args[9], C.POINTER(win.SecBufferDesc)).contents.pBuffers[0]
        assert output.BufferType == win._TOKEN
        C.memmove(output.pvBuffer, b"PRE", 3)
        output.cbBuffer = 3
        C.cast(args[10], C.POINTER(win.UINT)).contents.value = win._REQUIRED
        if bad_type:
            output.BufferType = win._DATA
        if invalid_pointer:
            output.pvBuffer = 1
        return status

    def complete(handle, descriptor):
        calls.append("complete")
        output = C.cast(descriptor, C.POINTER(win.SecBufferDesc)).contents.pBuffers[0]
        if not invalid_pointer and not bad_type:
            # Real SSPI is free to update the output token in its owned buffer.
            output.pvBuffer += 2
            C.memmove(output.pvBuffer, b"DONE", 4)
            output.cbBuffer = 4
        return completion_status

    context.lib = SimpleNamespace(
        InitializeSecurityContextW=initialize,
        CompleteAuthToken=complete,
    )
    return context, calls


@pytest.mark.parametrize("status,completed,needs_completion", [
    (win._OK, True, False),
    (win._CONTINUE, False, False),
    (win._COMPLETE, True, True),
    (win._COMPLETE_AND_CONTINUE, False, True),
])
def test_sspi_legal_completion_statuses(status, completed, needs_completion):
    context, calls = fake_context(status)
    value = context.step()
    assert value == (b"DONE" if needs_completion else b"PRE")
    assert context.complete is completed
    assert context._rounds == 1
    assert calls == (["initialize", "complete"] if needs_completion else ["initialize"])


def test_complete_auth_token_failure_fails_closed():
    context, calls = fake_context(win._COMPLETE, completion_status=-1)
    with pytest.raises(SPNEGOError, match="completion"):
        context.step()
    assert context._closed
    assert calls == ["initialize", "complete"]


@pytest.mark.parametrize("wrong_kind", ["pointer", "buffer_type"])
def test_sspi_does_not_read_untrusted_native_output_pointer(wrong_kind):
    context, _ = fake_context(
        win._OK,
        invalid_pointer=wrong_kind == "pointer",
        bad_type=wrong_kind == "buffer_type",
    )
    with pytest.raises(SPNEGOError, match="output token buffer"):
        context.step()
    assert context._closed


def test_unknown_failure_status_rejected_without_completing():
    context, calls = fake_context(-2146893056)
    with pytest.raises(SPNEGOError, match="authentication failed"):
        context.step()
    assert context._closed
    assert calls == ["initialize"]
