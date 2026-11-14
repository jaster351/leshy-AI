"""Windows SSPI Negotiate initiator backed by Secur32.dll, no extra process.

Uses only the current Windows logon credentials; never accepts a password
or requests credential delegation. Requires mutual auth, replay/sequence,
integrity and confidentiality before protected messages are possible.
"""
from __future__ import annotations

import ctypes as C
import os
from typing import Callable

from .native_spnego import SPNEGOError, MAX_MESSAGE, MAX_ROUNDS, MAX_TOKEN, _check_target, _check_token

UINT = C.c_uint32
PTR = C.c_void_p


class SecHandle(C.Structure):
    _fields_ = [("dwLower", PTR), ("dwUpper", PTR)]


class TimeStamp(C.Structure):
    _fields_ = [("LowPart", UINT), ("HighPart", C.c_int32)]


class SecBuffer(C.Structure):
    _fields_ = [("cbBuffer", UINT), ("BufferType", UINT), ("pvBuffer", PTR)]


class SecBufferDesc(C.Structure):
    _fields_ = [("ulVersion", UINT), ("cBuffers", UINT), ("pBuffers", C.POINTER(SecBuffer))]


class SecPkgSizes(C.Structure):
    _fields_ = [("cbMaxToken", UINT), ("cbMaxSignature", UINT),
                ("cbBlockSize", UINT), ("cbSecurityTrailer", UINT)]


# No ISC_REQ_DELEGATE. All requested security attributes must be returned.
_ISC_REQ = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010 | 0x00000800 | 0x00010000
_REQUIRED = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010 | 0x00010000
_OK, _CONTINUE = 0, 0x00090312
_TOKEN, _DATA, _PADDING = 2, 1, 9


def _desc(buffers) -> SecBufferDesc:
    return SecBufferDesc(0, len(buffers), C.cast(buffers, C.POINTER(SecBuffer)))


class WindowsSSPIContext:
    """Native SSPI handshake and encrypted Kerberos/Negotiate message protection."""

    def __init__(self, host: str, *, authorize: Callable[[], None]):
        if os.name != "nt":
            raise SPNEGOError("Windows SSPI is unavailable on this OS")
        self.host = _check_target(host)
        if not callable(authorize):
            raise PermissionError("Verified owner authorization required")
        self.authorize = authorize
        self.authorize()
        self.lib = C.WinDLL("secur32.dll")
        self._bind()
        self._cred, self._ctx, self._expiry = SecHandle(), SecHandle(), TimeStamp()
        self._have_cred = self._have_ctx = self._complete = self._closed = False
        self._rounds = self._send_seq = self._recv_seq = 0
        try:
            status = self.lib.AcquireCredentialsHandleW(
                None, "Negotiate", 2, None, None, None, None,
                C.byref(self._cred), C.byref(self._expiry),
            )
            if status != _OK:
                raise SPNEGOError("SSPI could not access current logon credentials")
            self._have_cred = True
        except BaseException:
            self.close()
            raise

    def _bind(self) -> None:
        h, d, u = C.POINTER(SecHandle), C.POINTER(SecBufferDesc), C.POINTER(UINT)
        self.lib.AcquireCredentialsHandleW.argtypes = [
            C.c_wchar_p, C.c_wchar_p, UINT, PTR, PTR, PTR, PTR, h, C.POINTER(TimeStamp),
        ]
        self.lib.AcquireCredentialsHandleW.restype = C.c_int32
        self.lib.InitializeSecurityContextW.argtypes = [
            h, h, C.c_wchar_p, UINT, UINT, UINT, d, UINT, h, d, u, C.POINTER(TimeStamp),
        ]
        self.lib.InitializeSecurityContextW.restype = C.c_int32
        self.lib.QueryContextAttributesW.argtypes = [h, UINT, PTR]
        self.lib.QueryContextAttributesW.restype = C.c_int32
        self.lib.EncryptMessage.argtypes = [h, UINT, d, UINT]
        self.lib.EncryptMessage.restype = C.c_int32
        self.lib.DecryptMessage.argtypes = [h, d, UINT, u]
        self.lib.DecryptMessage.restype = C.c_int32
        self.lib.DeleteSecurityContext.argtypes = [h]
        self.lib.DeleteSecurityContext.restype = C.c_int32
        self.lib.FreeCredentialsHandle.argtypes = [h]
        self.lib.FreeCredentialsHandle.restype = C.c_int32

    def _reauthorize(self) -> None:
        try:
            self.authorize()
        except BaseException:
            self.close()
            raise

    @property
    def complete(self) -> bool:
        return self._complete and not self._closed

    def step(self, incoming: bytes | None = None) -> bytes:
        if self._closed or self._complete or self._rounds >= MAX_ROUNDS:
            raise SPNEGOError("SSPI context is unavailable")
        if incoming is None:
            if self._rounds:
                raise SPNEGOError("SSPI server challenge required")
        else:
            _check_token(incoming)
            if not self._rounds:
                raise SPNEGOError("First SSPI token must be generated locally")
        self._reauthorize()
        input_desc = None
        if incoming is not None:
            input_store = C.create_string_buffer(incoming)
            incoming_arr = (SecBuffer * 1)(
                SecBuffer(len(incoming), _TOKEN, C.cast(input_store, PTR))
            )
            input_desc = _desc(incoming_arr)
        output_store = C.create_string_buffer(MAX_TOKEN)
        output_arr = (SecBuffer * 1)(
            SecBuffer(MAX_TOKEN, _TOKEN, C.cast(output_store, PTR))
        )
        output_desc = _desc(output_arr)
        actual = UINT()
        status = self.lib.InitializeSecurityContextW(
            C.byref(self._cred),
            C.byref(self._ctx) if self._have_ctx else None,
            "TERMSRV/" + self.host,
            _ISC_REQ, 0, 0x10,
            C.byref(input_desc) if input_desc is not None else None, 0,
            C.byref(self._ctx), C.byref(output_desc), C.byref(actual),
            C.byref(self._expiry),
        )
        self._rounds += 1
        if self._ctx.dwLower or self._ctx.dwUpper:
            self._have_ctx = True
        if status not in (_OK, _CONTINUE):
            self.close()
            raise SPNEGOError("SSPI target authentication failed")
        if output_arr[0].cbBuffer > MAX_TOKEN:
            self.close()
            raise SPNEGOError("SSPI output token exceeded limit")
        token = output_store.raw[:output_arr[0].cbBuffer]
        if status == _OK:
            if (actual.value & _REQUIRED) != _REQUIRED:
                self.close()
                raise SPNEGOError("SSPI lacks mutual authentication or confidentiality")
            self._complete = True
        self._reauthorize()
        return token

    def _sizes(self) -> SecPkgSizes:
        sizes = SecPkgSizes()
        result = self.lib.QueryContextAttributesW(C.byref(self._ctx), 0, C.byref(sizes))
        if (result != _OK or sizes.cbSecurityTrailer == 0 or
                sizes.cbSecurityTrailer > MAX_TOKEN or sizes.cbBlockSize > MAX_TOKEN):
            raise SPNEGOError("SSPI encryption sizes unavailable")
        return sizes

    def wrap(self, plaintext: bytes) -> bytes:
        if not self.complete:
            raise SPNEGOError("SSPI authentication incomplete")
        if not isinstance(plaintext, bytes) or not 0 < len(plaintext) <= MAX_MESSAGE:
            raise ValueError("Invalid SSPI plaintext")
        self._reauthorize()
        sizes = self._sizes()
        header = C.create_string_buffer(sizes.cbSecurityTrailer)
        data = C.create_string_buffer(plaintext)
        padding = C.create_string_buffer(max(1, sizes.cbBlockSize))
        buffers = (SecBuffer * 3)(
            SecBuffer(sizes.cbSecurityTrailer, _TOKEN, C.cast(header, PTR)),
            SecBuffer(len(plaintext), _DATA, C.cast(data, PTR)),
            SecBuffer(sizes.cbBlockSize, _PADDING, C.cast(padding, PTR)),
        )
        descriptor = _desc(buffers)
        result = self.lib.EncryptMessage(
            C.byref(self._ctx), 0, C.byref(descriptor), self._send_seq
        )
        if result != _OK or any((
            buffers[0].cbBuffer > sizes.cbSecurityTrailer,
            buffers[1].cbBuffer > len(plaintext),
            buffers[2].cbBuffer > sizes.cbBlockSize,
        )):
            self.close()
            raise SPNEGOError("SSPI message encryption failed")
        self._send_seq += 1
        self._reauthorize()
        return (header.raw[:buffers[0].cbBuffer] + data.raw[:buffers[1].cbBuffer]
                + padding.raw[:buffers[2].cbBuffer])

    def unwrap(self, ciphertext: bytes) -> bytes:
        if not self.complete:
            raise SPNEGOError("SSPI authentication incomplete")
        if not isinstance(ciphertext, bytes) or not 0 < len(ciphertext) <= MAX_MESSAGE + MAX_TOKEN:
            raise ValueError("Invalid SSPI ciphertext")
        self._reauthorize()
        sizes = self._sizes()
        prefix = sizes.cbSecurityTrailer
        if len(ciphertext) <= prefix:
            raise SPNEGOError("Truncated SSPI protected message")
        signature = C.create_string_buffer(ciphertext[:prefix])
        data = C.create_string_buffer(ciphertext[prefix:])
        buffers = (SecBuffer * 2)(
            SecBuffer(prefix, _TOKEN, C.cast(signature, PTR)),
            SecBuffer(len(ciphertext) - prefix, _DATA, C.cast(data, PTR)),
        )
        descriptor = _desc(buffers)
        qop = UINT()
        result = self.lib.DecryptMessage(
            C.byref(self._ctx), C.byref(descriptor), self._recv_seq, C.byref(qop)
        )
        # SECQOP_WRAP_NO_ENCRYPT would allow unsigned plaintext: fail closed.
        if (result != _OK or qop.value == 0x80000001 or
                buffers[1].cbBuffer > len(ciphertext) - prefix):
            self.close()
            raise SPNEGOError("SSPI message integrity/confidentiality failed")
        self._recv_seq += 1
        self._reauthorize()
        return data.raw[:buffers[1].cbBuffer]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._have_ctx:
            self.lib.DeleteSecurityContext(C.byref(self._ctx))
            self._have_ctx = False
        if self._have_cred:
            self.lib.FreeCredentialsHandle(C.byref(self._cred))
            self._have_cred = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


__all__ = ["WindowsSSPIContext"]
