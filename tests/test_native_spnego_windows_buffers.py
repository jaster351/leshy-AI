"""SSPI buffer safety tests: simulate provider output without a Windows host."""
from __future__ import annotations

import ctypes as C
from types import SimpleNamespace

import pytest

from leshy.native_spnego import SPNEGOError
from leshy.native_spnego_windows import (
    SecBufferDesc, SecPkgSizes, SecHandle, UINT, WindowsSSPIContext,
)


def mock_context(*, qop=0, pointer_offset=2):
    ctx = WindowsSSPIContext.__new__(WindowsSSPIContext)
    ctx._complete, ctx._closed, ctx._recv_seq = True, False, 0
    ctx._ctx = SecHandle()
    ctx._reauthorize = lambda: None
    ctx._sizes = lambda: SecPkgSizes(4096, 16, 1, 4)
    ctx.close = lambda: setattr(ctx, "_closed", True)

    def decrypt(handle, descriptor_ptr, seq, qop_ptr):
        descriptor = C.cast(descriptor_ptr, C.POINTER(SecBufferDesc)).contents
        assert descriptor.cBuffers == 2
        assert seq == 0
        signature, encrypted = descriptor.pBuffers[0], descriptor.pBuffers[1]
        assert signature.cbBuffer == 4
        assert encrypted.cbBuffer == 8
        base = encrypted.pvBuffer
        if 0 <= pointer_offset <= 6:
            C.memmove(base + pointer_offset, b"OK", 2)
        encrypted.pvBuffer = base + pointer_offset
        encrypted.cbBuffer = 2
        C.cast(qop_ptr, C.POINTER(UINT)).contents.value = qop
        return 0

    ctx.lib = SimpleNamespace(DecryptMessage=decrypt)
    return ctx


def test_sspi_uses_provider_decrypted_offset_not_start_of_ciphertext():
    ctx = mock_context()
    assert ctx.unwrap(b"HEAD" + b"abcdefgh") == b"OK"
    assert ctx._recv_seq == 1
    assert not ctx._closed


@pytest.mark.parametrize("qop", [1, 0x80000001, 0xFFFFFFFF])
def test_any_nondefault_sspi_qop_rejected(qop):
    ctx = mock_context(qop=qop)
    with pytest.raises(SPNEGOError, match="confidentiality"):
        ctx.unwrap(b"HEAD" + b"abcdefgh")
    assert ctx._closed
    assert ctx._recv_seq == 0


@pytest.mark.parametrize("offset", [-1, 7, 100])
def test_sspi_pointer_outside_owned_buffer_rejected(offset):
    ctx = mock_context(pointer_offset=offset)
    with pytest.raises(SPNEGOError, match="buffer"):
        ctx.unwrap(b"HEAD" + b"abcdefgh")
    assert ctx._closed


def test_sspi_replay_rejected_after_closed_context():
    ctx = mock_context(qop=1)
    with pytest.raises(SPNEGOError):
        ctx.unwrap(b"HEAD" + b"abcdefgh")
    with pytest.raises(SPNEGOError, match="incomplete"):
        ctx.unwrap(b"HEAD" + b"abcdefgh")
