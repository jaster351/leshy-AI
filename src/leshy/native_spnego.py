"""Native SPNEGO from the OS: Windows SSPI or POSIX GSSAPI.

No password handling, credential delegation, extra daemon or network socket.
The invoking owner must be independently verified on every operation.
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import ipaddress
import os
import re
from typing import Callable

MAX_TOKEN = 128 * 1024
MAX_MESSAGE = 64 * 1024
MAX_ROUNDS = 16
_REQUIRED = 2 | 4 | 8 | 16 | 32  # mutual auth, replay, sequence, privacy, integrity
_HOST_RE = re.compile(r"(?i)[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")


class SPNEGOError(RuntimeError):
    """Authentication failed, no raw credentials or tokens in diagnostics."""


def _check_target(host: str) -> str:
    if (not isinstance(host, str) or len(host) > 253 or not _HOST_RE.fullmatch(host)
            or "." not in host or ".." in host
            or any(x.startswith("-") or x.endswith("-") for x in host.split("."))):
        raise ValueError("Expected approved DNS name for TERMSRV service")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not valid Kerberos service names")
    return host.lower()


def _check_token(token: bytes, *, allow_empty: bool = False) -> None:
    if not isinstance(token, bytes) or len(token) > MAX_TOKEN or (not allow_empty and not token):
        raise ValueError("Invalid SPNEGO token")


class _Buffer(C.Structure):
    _fields_ = [("length", C.c_size_t), ("value", C.c_void_p)]


class _OID(C.Structure):
    _fields_ = [("length", C.c_uint32), ("elements", C.c_void_p)]


class UnixGSSContext:
    """Use the system GSSAPI SPNEGO mechanism and default Kerberos ticket cache."""

    def __init__(self, host: str, *, authorize: Callable[[], None]):
        self.host = _check_target(host)
        if not callable(authorize):
            raise PermissionError("Verified owner authorization is required")
        self.authorize = authorize
        self.authorize()
        name = ctypes.util.find_library("gssapi_krb5") or ctypes.util.find_library("gssapi")
        if not name:
            raise SPNEGOError("System GSSAPI library unavailable")
        try:
            self.lib = C.CDLL(name)
        except OSError as exc:
            raise SPNEGOError("System GSSAPI library unavailable") from exc
        self._bind()
        self._context, self._name = C.c_void_p(), C.c_void_p()
        self._closed, self._done, self._rounds = False, False, 0
        # Host-based service name 1.2.840.113554.1.2.1.4
        name_oid = bytes.fromhex("2a864886f71201020104")
        self._name_oid_mem = C.create_string_buffer(name_oid)
        self._name_oid = _OID(len(name_oid), C.cast(self._name_oid_mem, C.c_void_p))
        # Explicit SPNEGO 1.3.6.1.5.5.2; never silently use default raw Kerberos.
        mech_oid = bytes.fromhex("2b0601050502")
        self._mech_oid_mem = C.create_string_buffer(mech_oid)
        self._mech_oid = _OID(len(mech_oid), C.cast(self._mech_oid_mem, C.c_void_p))
        service, backing = self._input(("TERMSRV@" + self.host).encode("ascii"))
        minor = C.c_uint32()
        result = self.lib.gss_import_name(
            C.byref(minor), C.byref(service), C.byref(self._name_oid), C.byref(self._name)
        )
        if result != 0:
            raise SPNEGOError("GSSAPI could not import RDP target")

    def _bind(self) -> None:
        u, p, buf = C.c_uint32, C.c_void_p, C.POINTER(_Buffer)
        self.lib.gss_import_name.argtypes = [C.POINTER(u), buf, C.POINTER(_OID), C.POINTER(p)]
        self.lib.gss_import_name.restype = u
        self.lib.gss_init_sec_context.argtypes = [
            C.POINTER(u), p, C.POINTER(p), p, p, u, u, p, buf,
            C.POINTER(p), buf, C.POINTER(u), C.POINTER(u),
        ]
        self.lib.gss_init_sec_context.restype = u
        self.lib.gss_wrap.argtypes = [C.POINTER(u), p, C.c_int, u, buf, C.POINTER(C.c_int), buf]
        self.lib.gss_wrap.restype = u
        self.lib.gss_unwrap.argtypes = [C.POINTER(u), p, buf, buf, C.POINTER(C.c_int), C.POINTER(u)]
        self.lib.gss_unwrap.restype = u
        self.lib.gss_release_buffer.argtypes = [C.POINTER(u), buf]
        self.lib.gss_release_buffer.restype = u
        self.lib.gss_release_name.argtypes = [C.POINTER(u), C.POINTER(p)]
        self.lib.gss_release_name.restype = u
        self.lib.gss_delete_sec_context.argtypes = [C.POINTER(u), C.POINTER(p), buf]
        self.lib.gss_delete_sec_context.restype = u

    @staticmethod
    def _input(data: bytes):
        backing = C.create_string_buffer(data or b"\0")
        return _Buffer(len(data), C.cast(backing, C.c_void_p)), backing

    def _take_output(self, output: _Buffer) -> bytes:
        try:
            if output.length > MAX_TOKEN or (output.length and not output.value):
                raise SPNEGOError("Invalid or oversized GSS output")
            return C.string_at(output.value, output.length) if output.length else b""
        finally:
            if output.value:
                minor = C.c_uint32()
                self.lib.gss_release_buffer(C.byref(minor), C.byref(output))

    def _reauthorize(self):
        try:
            self.authorize()
        except BaseException:
            self.close()
            raise

    @property
    def complete(self) -> bool:
        return self._done and not self._closed

    def step(self, incoming: bytes | None = None) -> bytes:
        if self._closed or self._done or self._rounds >= MAX_ROUNDS:
            raise SPNEGOError("GSS context is unavailable")
        if incoming is None:
            if self._rounds:
                raise SPNEGOError("GSS server challenge required")
            incoming = b""
        elif self._rounds == 0:
            raise SPNEGOError("Initial GSS token must be locally generated")
        else:
            _check_token(incoming)
        self._reauthorize()
        source, backing = self._input(incoming)
        output = _Buffer()
        minor, flags, lifetime = C.c_uint32(), C.c_uint32(), C.c_uint32()
        result = self.lib.gss_init_sec_context(
            C.byref(minor), None, C.byref(self._context), self._name,
            C.byref(self._mech_oid), _REQUIRED, 0, None,
            C.byref(source) if incoming else None, None,
            C.byref(output), C.byref(flags), C.byref(lifetime),
        )
        self._rounds += 1
        token = self._take_output(output)
        if result not in (0, 1):
            self.close()
            raise SPNEGOError("GSSAPI rejected target authentication")
        if result == 0:
            if flags.value & _REQUIRED != _REQUIRED:
                self.close()
                raise SPNEGOError("GSSAPI missing mutual authentication or confidentiality")
            self._done = True
        self._reauthorize()
        return token

    def _protect(self, data: bytes, *, wrap: bool) -> bytes:
        if not self.complete:
            raise SPNEGOError("GSSAPI authentication not complete")
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_MESSAGE:
            raise ValueError("Invalid protected SPNEGO message")
        self._reauthorize()
        source, backing = self._input(data)
        output = _Buffer()
        minor, conf = C.c_uint32(), C.c_int()
        if wrap:
            result = self.lib.gss_wrap(
                C.byref(minor), self._context, 1, 0,
                C.byref(source), C.byref(conf), C.byref(output),
            )
        else:
            qop = C.c_uint32()
            result = self.lib.gss_unwrap(
                C.byref(minor), self._context, C.byref(source),
                C.byref(output), C.byref(conf), C.byref(qop),
            )
        if result != 0 or conf.value != 1:
            if output.value:
                self._take_output(output)
            self.close()
            raise SPNEGOError("GSSAPI message integrity or privacy failed")
        token = self._take_output(output)
        self._reauthorize()
        return token

    def wrap(self, plaintext: bytes) -> bytes:
        return self._protect(plaintext, wrap=True)

    def unwrap(self, ciphertext: bytes) -> bytes:
        return self._protect(ciphertext, wrap=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        minor = C.c_uint32()
        if self._context.value:
            output = _Buffer()
            self.lib.gss_delete_sec_context(C.byref(minor), C.byref(self._context), C.byref(output))
            if output.value:
                self.lib.gss_release_buffer(C.byref(minor), C.byref(output))
        if self._name.value:
            self.lib.gss_release_name(C.byref(minor), C.byref(self._name))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def new_native_spnego(host: str, *, authorize: Callable[[], None]):
    if os.name == "nt":
        from .native_spnego_windows import WindowsSSPIContext
        return WindowsSSPIContext(host, authorize=authorize)
    if os.name == "posix":
        return UnixGSSContext(host, authorize=authorize)
    raise SPNEGOError("Unsupported OS native SPNEGO provider")


__all__ = ["SPNEGOError", "UnixGSSContext", "new_native_spnego"]
