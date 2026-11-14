"""Owner-bound CredSSP SPNEGO + TLS public-key binding without credentials.

This state machine stops at server binding. It cannot send authInfo or
establish a logged-in RDP desktop. The SPNEGO engine must provide authenticated
confidentiality before any pubKeyAuth is constructed or verified.
"""
from __future__ import annotations

from typing import Callable, Protocol

from .rdp_credssp_binding import prepare_credssp_binding
from .rdp_credssp_tsrequest import TSRequest, decode_tsrequest, encode_tsrequest


class AuthenticationError(RuntimeError):
    """CredSSP flow rejected without logging raw challenge tokens."""


class NativeMechanism(Protocol):
    @property
    def complete(self) -> bool: ...
    def step(self, incoming: bytes | None = None) -> bytes: ...
    def wrap(self, plaintext: bytes) -> bytes: ...
    def unwrap(self, ciphertext: bytes) -> bytes: ...
    def close(self) -> None: ...


class CredSSPClient:
    """SPNEGO-only NLA preparation with owner revalidation on every phase."""

    def __init__(self, mechanism: NativeMechanism, tls_cert_der: bytes,
                 pinned_tls_sha256: str, *, authorize: Callable[[], None]):
        if not callable(authorize):
            raise PermissionError("Verified owner authorization required")
        authorize()
        self._mechanism = mechanism
        self._authorize = authorize
        # Certificate MUST be the actual TLS peer DER and pin pre-enrolled.
        self._binding = prepare_credssp_binding(tls_cert_der, pinned_tls_sha256)
        self._state, self._rounds, self._closed = "new", 0, False

    @property
    def server_bound(self) -> bool:
        return self._state == "bound" and not self._closed

    @property
    def credentials_delegated(self) -> bool:
        return False

    def _abort(self):
        self.close()
        raise AuthenticationError("CredSSP authentication failed")

    def _seal_binding(self, token: bytes = b"") -> bytes:
        if not self._mechanism.complete:
            self._abort()
        self._authorize()
        sealed = self._mechanism.wrap(self._binding.client_hash)
        if not isinstance(sealed, bytes) or not 0 < len(sealed) <= 128 * 1024:
            self._abort()
        self._state = "waiting_for_server_binding"
        return encode_tsrequest(TSRequest(
            version=6, nego_tokens=(token,) if token else (),
            pub_key_auth=sealed, client_nonce=self._binding.nonce,
        ))

    def start(self) -> bytes:
        if self._state != "new" or self._closed:
            raise AuthenticationError("CredSSP handshake already started")
        try:
            self._authorize()
            token = self._mechanism.step()
            if not isinstance(token, bytes) or not 0 < len(token) <= 128 * 1024:
                self._abort()
            self._rounds = 1
            if self._mechanism.complete:
                return self._seal_binding(token)
            self._state = "negotiating"
            return encode_tsrequest(TSRequest(version=6, nego_tokens=(token,)))
        except BaseException:
            self.close()
            raise

    def receive(self, packet: bytes) -> bytes | None:
        if self._closed or self._state not in ("negotiating", "waiting_for_server_binding"):
            raise AuthenticationError("Unexpected CredSSP response")
        try:
            self._authorize()
            response = decode_tsrequest(packet)
            if (response.version not in (5, 6)
                    or response.error_code is not None
                    or response.client_nonce is not None):
                self._abort()
            if self._state == "negotiating":
                if response.pub_key_auth is not None or len(response.nego_tokens) != 1:
                    self._abort()
                if self._rounds >= 16:
                    self._abort()
                token = self._mechanism.step(response.nego_tokens[0])
                self._rounds += 1
                if not isinstance(token, bytes) or len(token) > 128 * 1024:
                    self._abort()
                if self._mechanism.complete:
                    return self._seal_binding(token)
                if not token:
                    self._abort()
                return encode_tsrequest(TSRequest(version=6, nego_tokens=(token,)))
            if response.nego_tokens or response.pub_key_auth is None:
                self._abort()
            unsealed = self._mechanism.unwrap(response.pub_key_auth)
            self._binding.verify_unsealed_server_hash(unsealed)
            self._authorize()
            self._state = "bound"
            return None
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._state = "closed"
            self._mechanism.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


__all__ = ["CredSSPClient", "AuthenticationError"]
