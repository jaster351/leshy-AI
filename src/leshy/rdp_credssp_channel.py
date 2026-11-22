"""Pinned TLS RDP channel for CredSSP binding and separately approved credentials.

Unlike rdp_tls_probe, this keeps the SAME socket through X.224, TLS and
the protected CredSSP TSRequest exchange. A separately owner-approved
internal credential step may follow successful binding. This is NOT a
confirmed Windows logon or an RDP desktop session.

This internal transport has no HTTP, Telegram or LLM entry point. Its
authorize callback must verify a provider-authenticated owner, an enrolled
literal IP + DNS service identity pair, an RDP permission and the TLS pin.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import math
import re
import socket
import ssl
import time
import threading
from typing import Callable, Any

from .rdp_tls_probe import _negotiate, _time_left
from .rdp_credssp_tsrequest import MAX_MESSAGE, decode_tsrequest
from .rdp_credssp_negotiator import CredSSPClient
from .rdp_credssp_credentials import (
    RDPCredentials, validate_sealed_authinfo_tsrequest,
)
from .native_spnego import _check_target, new_native_spnego
from .rdp_confirm_active import ConfirmActiveReceipt, build_confirm_active
from .rdp_bitmap_update import RDPBitmapRectangle, parse_bitmap_update
from .rdp_bitmap_compositor import RDPBitmapCompositor, RDPComposedFrame
from .rdp_graphics_stream import (
    GraphicsStreamPolicy, GraphicsStreamResult, RDPGraphicsStopped,
    read_graphics_tpkt,
)
from .rdp_finalization import (
    RDPFinalizationError, RDPFinalizationStatus,
    client_finalization_packets, parse_server_finalization,
)
from .rdp_server_activation import (
    MAX_SERVER_PACKET, RDPActivationError, RDPServerActivation,
    read_server_mcs_data, parse_valid_client_license, parse_demand_active,
)
from .rdp_client_info import (
    RDPClientInfoReceipt, build_client_info_pdu,
)
from .rdp_mcs_channels import (
    MCSChannelError, MCSJoinedChannels, erect_domain_request,
    attach_user_request, parse_attach_user_confirm, channel_join_request,
    parse_channel_join_confirm, required_channel_ids,
)
from .rdp_mcs_basic import (
    MCSError, MAX_PACKET as MAX_MCS_PACKET, ServerMCSSettings,
    build_mcs_connect_initial, parse_mcs_connect_response,
)

_CERT_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
_MAX_ROUND_TRIPS = 18


class RDPChannelError(RuntimeError):
    """RDP channel failure without raw wire data or credentials."""


@dataclass(frozen=True)
class RDPBindingStatus:
    selected_protocol: str
    tls_version: str
    pinned_certificate: bool
    server_bound: bool
    credentials_delegated: bool = False
    rdp_desktop_available: bool = False


def _read_exact(tls: ssl.SSLSocket, n: int, deadline: float) -> bytes:
    parts = []
    while n:
        tls.settimeout(_time_left(deadline))
        chunk = tls.recv(n)
        if not chunk:
            raise RDPChannelError("RDP peer closed the protected channel")
        parts.append(chunk)
        n -= len(chunk)
    return b"".join(parts)


def read_tsrequest(tls: ssl.SSLSocket, *, deadline: float) -> bytes:
    """Read exactly ONE canonical DER SEQUENCE, even across fragmented TLS reads."""
    header = _read_exact(tls, 2, deadline)
    if header[0] != 0x30:
        raise RDPChannelError("Unexpected CredSSP DER envelope")
    first = header[1]
    if first < 0x80:
        length = first
    else:
        width = first & 0x7F
        if not 1 <= width <= 3:
            raise RDPChannelError("Invalid CredSSP DER length encoding")
        extra = _read_exact(tls, width, deadline)
        if extra[0] == 0:
            raise RDPChannelError("Noncanonical CredSSP DER length")
        length = int.from_bytes(extra, "big")
        if length < 128:
            raise RDPChannelError("Noncanonical CredSSP DER length")
        header += extra
    if not length or len(header) + length > MAX_MESSAGE:
        raise RDPChannelError("CredSSP frame exceeds size policy")
    packet = header + _read_exact(tls, length, deadline)
    decode_tsrequest(packet)  # Reject malformed or credential-bearing input.
    return packet


class PinnedRDPCredSSPChannel:
    """One owner-scoped TCP/TLS channel with a bounded SPNEGO binding exchange."""

    def __init__(
        self, tls: ssl.SSLSocket, certificate_der: bytes,
        *, ip: str, dns_name: str, pin: str, selected_protocol: str,
        authorize: Callable[[str, str], None], deadline: float,
    ):
        self._tls = tls
        self._certificate = certificate_der
        self._ip, self._dns, self._pin = ip, dns_name, pin
        self._selected, self._authorize_callback = selected_protocol, authorize
        self._deadline = deadline
        self._credssp: CredSSPClient | None = None
        self._closed = False
        self._mcs_attempted = False
        self._mcs_settings: ServerMCSSettings | None = None
        self._mcs_join_attempted = False
        self._mcs_joined: MCSJoinedChannels | None = None
        self._client_info_attempted = False
        self._client_info_receipt: RDPClientInfoReceipt | None = None
        self._server_activation_attempted = False
        self._server_activation: RDPServerActivation | None = None
        self._mcs_display: tuple[int, int] | None = None
        self._confirm_active_attempted = False
        self._confirm_active_receipt: ConfirmActiveReceipt | None = None
        self._finalization_attempted = False
        self._finalization_status: RDPFinalizationStatus | None = None
        self._bitmap_surface: RDPBitmapCompositor | None = None
        # TLS receives must never interleave between concurrent callers.
        self._bitmap_receive_lock = threading.Lock()
        self._graphics_stop = threading.Event()
        self._graphics_started = False

    def _check(self) -> None:
        if self._closed:
            raise RDPChannelError("RDP channel closed")
        _time_left(self._deadline)
        try:
            self._authorize_callback(self._ip, self._dns)
        except BaseException:
            self.close()
            raise

    def local_client_address(self) -> str:
        """Literal local IPv4/IPv6 observed on the pinned TLS socket."""
        self._check()
        try:
            return str(ipaddress.ip_address(self._tls.getsockname()[0]))
        except (OSError, ValueError, IndexError, TypeError) as exc:
            raise RDPChannelError("Cannot verify native RDP client address") from exc

    @property
    def server_bound(self) -> bool:
        return bool(not self._closed and self._credssp and self._credssp.server_bound)

    @property
    def credentials_delegated(self) -> bool:
        return False

    def _send(self, packet: bytes) -> None:
        self._check()
        if not isinstance(packet, bytes) or not 0 < len(packet) <= MAX_MESSAGE:
            raise RDPChannelError("Invalid outbound CredSSP frame")
        parsed = decode_tsrequest(packet)
        if parsed.error_code is not None:
            raise RDPChannelError("Client cannot transmit CredSSP failure codes")
        self._tls.settimeout(_time_left(self._deadline))
        self._tls.sendall(packet)
        self._check()

    def _receive(self) -> bytes:
        self._check()
        packet = read_tsrequest(self._tls, deadline=self._deadline)
        self._check()
        return packet

    def authenticate_server(self, *, mechanism_factory: Callable[..., Any] | None = None) -> RDPBindingStatus:
        """Perform GSS/SPNEGO + encrypted TLS-public-key proof, WITHOUT authInfo.

        Returns a server-binding status only. The socket stays open inside
        this context manager. It cannot yet perform Windows logon or RDP IO.
        """
        if self._credssp is not None:
            raise RDPChannelError("CredSSP binding already attempted")
        self._check()
        maker = mechanism_factory or new_native_spnego
        mechanism = None
        try:
            mechanism = maker(self._dns, authorize=self._check)
            self._credssp = CredSSPClient(
                mechanism, self._certificate, self._pin, authorize=self._check
            )
            outgoing = self._credssp.start()
            for _ in range(_MAX_ROUND_TRIPS):
                self._send(outgoing)
                incoming = self._receive()
                outgoing = self._credssp.receive(incoming)
                if outgoing is None:
                    if not self._credssp.server_bound:
                        raise RDPChannelError("CredSSP server binding incomplete")
                    self._check()
                    return RDPBindingStatus(
                        self._selected, str(self._tls.version() or "unknown"),
                        pinned_certificate=True, server_bound=True,
                    )
            raise RDPChannelError("CredSSP exchange exceeded round limit")
        except BaseException:
            self.close()
            if mechanism is not None and self._credssp is None:
                mechanism.close()
            raise

    def delegate_password_credentials(
        self, credentials: RDPCredentials, *,
        authorize_delegation: Callable[[str, str], None],
    ) -> RDPBindingStatus:
        """Transmit exactly ONE owner-approved, GSS-encrypted authInfo frame.

        This is an internal API. The owner must explicitly approve supplying
        credentials to the independently pinned RDP host, and the caller must
        already have a trusted local credential source. It does not implement
        Windows logon/MCS, or prove the target accepted the credential.
        """
        if not callable(authorize_delegation):
            raise PermissionError("Separate owner approval for RDP credentials required")
        if (self._credssp is None or not self._credssp.server_bound
                or self._credssp.credentials_delegated):
            raise RDPChannelError("RDP server binding must finish before credential transfer")

        def revalidate_delegation() -> None:
            self._check()
            authorize_delegation(self._ip, self._dns)
            self._check()

        try:
            revalidate_delegation()
            packet = self._credssp.prepare_password_delegation(
                credentials, authorize_delegation=revalidate_delegation,
            )
            # Inbound/ordinary TSRequest decoding STILL forbids authInfo.
            # Only this frame freshly constructed from authenticated GSS Wrap
            # may be sent through the dedicated credential path.
            validate_sealed_authinfo_tsrequest(packet)
            revalidate_delegation()
            self._tls.settimeout(_time_left(self._deadline))
            self._tls.sendall(packet)
            self._check()
            self._credssp.mark_password_sent()
            return RDPBindingStatus(
                self._selected, str(self._tls.version() or "unknown"),
                pinned_certificate=True, server_bound=True,
                credentials_delegated=True, rdp_desktop_available=False,
            )
        except BaseException:
            # Even on send failure the peer might have received authInfo.
            # Never retry automatically and never re-use this GSS channel.
            self.close()
            raise


    def exchange_mcs_basic_settings(
        self, width: int = 1280, height: int = 720,
    ) -> ServerMCSSettings:
        """Perform only RDP MCS Basic Settings Exchange on existing TLS.

        No packet is sent until the separately approved CredSSP credentials
        were transferred on this SAME verified owner-scoped connection.
        Server MCS response confirms settings only, NOT Windows desktop login.
        """
        if (self._closed or self._mcs_attempted or self._credssp is None
                or not self._credssp.credentials_delegated):
            raise RDPChannelError("MCS requires approved CredSSP credential transfer")
        self._mcs_attempted = True  # single attempt, including failures
        try:
            self._check()
            # Hybrid-EX uses a four-byte early authorization result after the
            # CredSSP exchange. Never treat access-denied as a basic setting.
            if self._selected == "CredSSP-EX":
                result = _read_exact(self._tls, 4, self._deadline)
                self._check()
                if result != b"\x00\x00\x00\x00":
                    raise PermissionError("RDP early user authorization was not granted")
            protocol = {"CredSSP": 2, "CredSSP-EX": 8}.get(self._selected)
            if protocol is None:
                raise RDPChannelError("Invalid MCS security protocol")
            request = build_mcs_connect_initial(
                width, height, selected_protocol=protocol
            )
            self._check()
            self._tls.settimeout(_time_left(self._deadline))
            self._tls.sendall(request)
            self._check()
            header = _read_exact(self._tls, 4, self._deadline)
            if header[:2] != b"\x03\x00":
                raise MCSError("Invalid MCS TPKT response")
            size = int.from_bytes(header[2:4], "big")
            if not 18 <= size <= MAX_MCS_PACKET:
                raise MCSError("MCS TPKT length outside configured limit")
            response = header + _read_exact(self._tls, size - 4, self._deadline)
            settings = parse_mcs_connect_response(response)
            self._check()
            self._mcs_settings = settings
            self._mcs_display = (width, height)
            return settings
        except BaseException:
            self.close()
            raise

    def _read_mcs_confirm(self, size: int) -> bytes:
        """Read a single bounded TPKT; never consume another MCS packet."""
        self._check()
        header = _read_exact(self._tls, 4, self._deadline)
        if header[:2] != b"\x03\x00" or int.from_bytes(header[2:4], "big") != size:
            raise MCSChannelError("Unexpected MCS confirmation frame length")
        packet = header + _read_exact(self._tls, size - 4, self._deadline)
        self._check()
        return packet

    def join_mcs_channels(self) -> MCSJoinedChannels:
        """Join ONLY user and I/O channels after successful Basic Settings.

        MCS Channel Join is sequential, with a checked server response for
        each channel. Does NOT mean screen or input is available.
        """
        if (self._closed or self._mcs_join_attempted or self._mcs_settings is None
                or self._credssp is None or not self._credssp.credentials_delegated):
            raise RDPChannelError("MCS channels require completed owner-approved basic settings")
        self._mcs_join_attempted = True  # no retries after partial sends
        try:
            self._check()
            self._tls.settimeout(_time_left(self._deadline))
            self._tls.sendall(erect_domain_request())
            self._check()
            self._tls.settimeout(_time_left(self._deadline))
            self._tls.sendall(attach_user_request())
            user_id = parse_attach_user_confirm(self._read_mcs_confirm(11))
            channels = required_channel_ids(user_id, self._mcs_settings.io_channel_id)
            for target in channels:
                self._check()
                self._tls.settimeout(_time_left(self._deadline))
                self._tls.sendall(channel_join_request(user_id, target))
                self._check()
                parse_channel_join_confirm(
                    self._read_mcs_confirm(15),
                    user_id=user_id, requested_channel=target,
                )
            self._check()
            result = MCSJoinedChannels(user_id, channels[1], channels)
            self._mcs_joined = result
            return result
        except BaseException:
            self.close()
            raise

    def send_client_info(self, *, client_address: str) -> RDPClientInfoReceipt:
        """Send minimal non-autologon Client Info on the joined I/O channel.

        This is a one-shot internal protocol operation. It cannot introduce a
        password or claim the server logged in. The caller must provide the
        literal client-side socket address from trusted local runtime state.
        """
        if (self._closed or self._client_info_attempted or self._mcs_joined is None
                or self._credssp is None or not self._credssp.credentials_delegated):
            raise RDPChannelError("Client Info requires joined owner-approved MCS channels")
        self._client_info_attempted = True
        try:
            self._check()
            packet = build_client_info_pdu(
                self._mcs_joined.user_channel_id,
                self._mcs_joined.io_channel_id,
                client_address=client_address,
            )
            self._check()
            self._tls.settimeout(_time_left(self._deadline))
            self._tls.sendall(packet)
            self._check()
            receipt = RDPClientInfoReceipt(
                self._mcs_joined.user_channel_id,
                self._mcs_joined.io_channel_id,
            )
            self._client_info_receipt = receipt
            return receipt
        except BaseException:
            # Failure after any part of a packet may have reached the server.
            # Do not retry automatically or reuse the compromised channel.
            self.close()
            raise

    def _read_activation_tpkt(self) -> bytes:
        """One bounded, complete post-Client-Info packet on the pinned TLS socket."""
        self._check()
        header = _read_exact(self._tls, 4, self._deadline)
        if header[:2] != b"\x03\x00":
            raise RDPActivationError("Invalid activation TPKT header")
        size = int.from_bytes(header[2:4], "big")
        if not 16 <= size <= MAX_SERVER_PACKET:
            raise RDPActivationError("Invalid activation TPKT size")
        packet = header + _read_exact(self._tls, size - 4, self._deadline)
        self._check()
        return packet

    def receive_server_activation(self) -> RDPServerActivation:
        """Read approved personal-RDP licensing and Demand Active, not a desktop.

        Only the STATUS_VALID_CLIENT licensing flow is implemented.
        Unsupported RDPELE license requests fail closed; this method never
        guesses a valid license and never automatically retries login.
        """
        if (self._closed or self._server_activation_attempted
                or self._client_info_receipt is None or self._mcs_joined is None
                or self._credssp is None or not self._credssp.credentials_delegated):
            raise RDPChannelError("RDP server activation requires completed Client Info")
        self._server_activation_attempted = True
        try:
            self._check()
            user, payload = read_server_mcs_data(
                self._read_activation_tpkt(),
                expected_io_channel=self._mcs_joined.io_channel_id,
            )
            parse_valid_client_license(payload)
            self._check()
            demand_user, demand_data = read_server_mcs_data(
                self._read_activation_tpkt(),
                expected_io_channel=self._mcs_joined.io_channel_id,
            )
            if demand_user != user:
                raise RDPActivationError("Activation server user channel changed")
            share_id, types = parse_demand_active(demand_data)
            self._check()
            result = RDPServerActivation(
                share_id=share_id, server_mcs_user_id=user,
                io_channel_id=self._mcs_joined.io_channel_id,
                capability_types=types,
            )
            self._server_activation = result
            return result
        except BaseException:
            self.close()
            raise

    def send_confirm_active(self) -> ConfirmActiveReceipt:
        """Send bounded Confirm Active capabilities; no graphics or input yet.

        Only after the licensed server's Demand Active on the SAME pinned
        TLS connection. No legacy security header is used under enhanced
        security. Once sent, the channel remains in unfinalized state.
        """
        if (self._closed or self._confirm_active_attempted
                or self._server_activation is None or self._mcs_display is None
                or self._mcs_joined is None or self._client_info_receipt is None
                or self._credssp is None or not self._credssp.credentials_delegated):
            raise RDPChannelError("Confirm Active requires licensed MCS and Demand Active")
        self._confirm_active_attempted = True
        try:
            self._check()
            packet = build_confirm_active(
                share_id=self._server_activation.share_id,
                user_channel_id=self._mcs_joined.user_channel_id,
                io_channel_id=self._mcs_joined.io_channel_id,
                width=self._mcs_display[0], height=self._mcs_display[1],
                color_depth=24,
                server_capability_types=self._server_activation.capability_types,
            )
            self._check()
            self._tls.settimeout(_time_left(self._deadline))
            self._tls.sendall(packet)
            self._check()
            receipt = ConfirmActiveReceipt(
                share_id=self._server_activation.share_id,
                user_channel_id=self._mcs_joined.user_channel_id,
                io_channel_id=self._mcs_joined.io_channel_id,
            )
            self._confirm_active_receipt = receipt
            return receipt
        except BaseException:
            # A partial send can change server state. No blind retry or
            # receipt publication; revoke the entire TLS/GSS channel.
            self.close()
            raise

    def finalize_mcs_connection(self) -> RDPFinalizationStatus:
        """Sequentially finish the bounded TLS/MCS control handshake.

        The outgoing PDUs (Sync, Cooperate, Request Control, Font List) are
        ordered independently of server replies by MS-RDPBCGR. Four inbound
        acknowledgements must then be individually verified. This does NOT
        create a framebuffer, grant RDP keyboard/mouse, or prove Windows logon.
        """
        if (self._closed or self._finalization_attempted
                or self._confirm_active_receipt is None
                or self._server_activation is None or self._mcs_joined is None
                or self._credssp is None or not self._credssp.credentials_delegated):
            raise RDPChannelError("RDP finalization requires completed Confirm Active")
        self._finalization_attempted = True
        try:
            activation = self._server_activation
            user = self._mcs_joined.user_channel_id
            io = self._mcs_joined.io_channel_id
            if (activation.io_channel_id != io
                    or self._confirm_active_receipt.share_id != activation.share_id
                    or self._confirm_active_receipt.user_channel_id != user
                    or self._confirm_active_receipt.io_channel_id != io):
                raise RDPFinalizationError("Conflicting RDP activation identities")
            packets = client_finalization_packets(
                share_id=activation.share_id, user_channel_id=user, io_channel_id=io
            )
            for packet in packets:
                self._check()
                self._tls.settimeout(_time_left(self._deadline))
                self._tls.sendall(packet)
                self._check()
            stages = ("synchronize", "cooperate", "granted_control", "font_map")
            for stage in stages:
                self._check()
                packet = self._read_activation_tpkt()
                parse_server_finalization(
                    packet, expected=stage, share_id=activation.share_id,
                    server_user_id=activation.server_mcs_user_id,
                    user_channel_id=user, io_channel_id=io,
                )
                self._check()
            status = RDPFinalizationStatus(
                activation.share_id, user, io, stages,
            )
            self._finalization_status = status
            return status
        except BaseException:
            # Uncertain partial sends/receives are never replayed automatically.
            self.close()
            raise

    def receive_bitmap_rectangles(self) -> tuple[RDPBitmapRectangle, ...]:
        """Read ONE strictly decoded slow-path bitmap update, not a desktop.

        Only after validated finalization on this exact pinned TLS socket.
        The returned RGB rectangles are partial patches, not a framebuffer
        receipt and never proof that remote keyboard/mouse is safe to use.
        Compressed, clipped or unrecognized PDUs fail closed until supported.
        """
        if (self._closed or self._finalization_status is None
                or self._server_activation is None or self._mcs_joined is None
                or self._mcs_display is None or self._credssp is None
                or not self._credssp.credentials_delegated):
            raise RDPChannelError("Bitmap update requires finalized owner-approved RDP")
        try:
            self._check()
            packet = self._read_activation_tpkt()
            result = parse_bitmap_update(
                packet,
                share_id=self._server_activation.share_id,
                server_user_id=self._server_activation.server_mcs_user_id,
                io_channel_id=self._mcs_joined.io_channel_id,
                desktop_width=self._mcs_display[0],
                desktop_height=self._mcs_display[1],
            )
            self._check()
            return result
        except BaseException:
            # Do not expose partial graphics or continue on unknown codecs.
            self.close()
            raise

    def receive_bitmap_frame(self) -> RDPComposedFrame | None:
        """Accumulate one real bitmap update; publish ONLY complete coverage.

        This internal read-only output is neither a verified screenshot
        receipt nor permission for keyboard/mouse. It cannot paint unknown
        pixels or decode compressed Bitmap Updates. Network access remains
        serialized and owner-checked within the existing pinned TLS channel.
        """
        with self._bitmap_receive_lock:
            if (self._closed or self._finalization_status is None
                    or self._mcs_display is None):
                raise RDPChannelError("RGB framebuffer requires finalized RDP")
            try:
                self._check()
                if self._bitmap_surface is None:
                    self._bitmap_surface = RDPBitmapCompositor(
                        self._mcs_display[0], self._mcs_display[1],
                    )
                patches = self.receive_bitmap_rectangles()
                frame = self._bitmap_surface.apply(patches)
                self._check()
                return frame
            except BaseException:
                self.close()
                raise

    def stop_graphics_stream(self) -> None:
        """Request a trusted, bounded stream stop; safe during TLS reads.

        A stopping caller cannot grant input permissions. The receiver checks
        the event at most 200 ms between socket read attempts and closes the
        pinned TLS/GSS transport before it returns.
        """
        self._graphics_stop.set()

    def _check_graphics_owner(self) -> None:
        """Reauthorize without reusing the expired CredSSP handshake deadline."""
        if self._closed:
            raise RDPChannelError("RDP graphics channel closed")
        if self._graphics_stop.is_set():
            raise RDPGraphicsStopped("RDP graphics stream stopped")
        try:
            self._authorize_callback(self._ip, self._dns)
        except BaseException:
            self.close()
            raise

    def stream_bitmap_frames(
        self,
        on_frame: Callable[[RDPComposedFrame], None],
        *,
        policy: GraphicsStreamPolicy | None = None,
    ) -> GraphicsStreamResult:
        """Bounded, synchronous read-only graphics stream on SAME pinned TLS.

        An owner-approved caller may run this method in its own thread and
        invoke stop_graphics_stream() from another. No daemon is spawned.
        Each packet gets a new idle deadline independent of the short
        CredSSP handshake deadline; owner access is rechecked on EVERY TLS
        fragment, before compositing and immediately before frame delivery.

        Full RGB images are delivered only after complete pixel coverage.
        Unsupported slow/fast-path messages close the session; this is not
        a finished Windows interoperability layer or authorized RDP input.
        """
        if not callable(on_frame):
            raise TypeError("Read-only RDP graphics requires a frame consumer")
        if policy is None:
            policy = GraphicsStreamPolicy()
        if not isinstance(policy, GraphicsStreamPolicy):
            raise TypeError("Invalid RDP graphics stream policy")
        with self._bitmap_receive_lock:
            if (self._closed or self._graphics_started
                    or self._finalization_status is None
                    or self._server_activation is None or self._mcs_joined is None
                    or self._mcs_display is None or self._credssp is None
                    or not self._credssp.credentials_delegated):
                raise RDPChannelError("Graphics stream requires finalized owner-approved RDP")
            self._graphics_started = True  # one stream per pinned TLS session
            started = time.monotonic()
            received = published = 0
            last_published: float | None = None
            reason = "update_limit"
            try:
                # One last check against the original handshake deadline
                # before switching to separate, bounded post-handshake reads.
                self._check()
                self._bitmap_surface = RDPBitmapCompositor(
                    self._mcs_display[0], self._mcs_display[1]
                )
                until = started + policy.max_duration
                for _ in range(policy.max_updates):
                    if self._graphics_stop.is_set():
                        reason = "stopped"
                        break
                    if time.monotonic() >= until:
                        reason = "duration_limit"
                        break
                    deadline = min(until, time.monotonic() + policy.idle_timeout)
                    packet = read_graphics_tpkt(
                        self._tls, deadline=deadline,
                        reauthorize=self._check_graphics_owner,
                        stopped=self._graphics_stop.is_set,
                    )
                    self._check_graphics_owner()
                    patches = parse_bitmap_update(
                        packet,
                        share_id=self._server_activation.share_id,
                        server_user_id=self._server_activation.server_mcs_user_id,
                        io_channel_id=self._mcs_joined.io_channel_id,
                        desktop_width=self._mcs_display[0],
                        desktop_height=self._mcs_display[1],
                    )
                    self._check_graphics_owner()
                    frame = self._bitmap_surface.apply(patches)
                    received += 1
                    self._check_graphics_owner()
                    if frame is not None:
                        now = time.monotonic()
                        if (last_published is None
                                or now - last_published >= policy.min_frame_interval):
                            self._check_graphics_owner()
                            on_frame(frame)
                            published += 1
                            last_published = time.monotonic()
                return GraphicsStreamResult(
                    received, published, reason, time.monotonic() - started
                )
            except RDPGraphicsStopped:
                return GraphicsStreamResult(
                    received, published, "stopped", time.monotonic() - started
                )
            finally:
                # Even a successful max-updates exit ends this sensitive
                # TLS/GSS session. A new connection needs fresh authorization.
                self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._credssp is not None:
                self._credssp.close()
        finally:
            try:
                self._tls.close()
            finally:
                if self._bitmap_surface is not None:
                    # Revoke local coverage if the pinned connection closes.
                    self._bitmap_surface.reset()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def connect_verified_rdp_credssp(
    node_ip: str, service_dns_name: str, enrolled_tls_sha256: str, *,
    authorize: Callable[[str, str], None],
    timeout: float = 15.0,
    port: int = 3389,
) -> PinnedRDPCredSSPChannel:
    """Connect to a pre-enrolled owner-approved IP+DNS SPN+exact TLS cert pin.

    Passing a pin read from THIS connection is forbidden; enrollment must
    occur independently beforehand. On errors all sockets are closed.
    """
    if not callable(authorize):
        raise PermissionError("Verified owner authorization required")
    if type(node_ip) is not str or not node_ip or len(node_ip) > 64:
        raise ValueError("RDP target must be a bounded literal IP string")
    try:
        ip = str(ipaddress.ip_address(node_ip))
    except ValueError as exc:
        raise ValueError("RDP target must be a literal IP") from exc
    dns = _check_target(service_dns_name)
    if type(enrolled_tls_sha256) is not str or not _CERT_SHA256.fullmatch(enrolled_tls_sha256):
        raise PermissionError("Independently enrolled RDP TLS certificate pin required")
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 1 <= timeout <= 30):
        raise ValueError("Invalid total RDP timeout")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid RDP TCP port")
    pin = enrolled_tls_sha256.lower()
    authorize(ip, dns)
    deadline = time.monotonic() + timeout
    raw = None
    tls = None
    try:
        raw = socket.create_connection((ip, port), timeout=_time_left(deadline))
        raw.settimeout(_time_left(deadline))
        selected = _negotiate(raw, deadline=deadline)
        if selected not in ("CredSSP", "CredSSP-EX"):
            raise RDPChannelError("RDP server did not select CredSSP authentication")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        # Exact independently enrolled leaf certificate SHA-256 pin is mandatory.
        context.verify_mode = ssl.CERT_NONE
        raw.settimeout(_time_left(deadline))
        tls = context.wrap_socket(raw, server_hostname=None)
        raw = None
        _time_left(deadline)
        certificate = tls.getpeercert(binary_form=True)
        if not isinstance(certificate, bytes) or not certificate:
            raise PermissionError("RDP server did not supply a TLS certificate")
        if not hmac.compare_digest(hashlib.sha256(certificate).hexdigest(), pin):
            raise PermissionError("RDP TLS certificate fingerprint does not match enrollment")
        authorize(ip, dns)
        _time_left(deadline)
        channel = PinnedRDPCredSSPChannel(
            tls, certificate, ip=ip, dns_name=dns, pin=pin,
            selected_protocol=selected, authorize=authorize, deadline=deadline
        )
        tls = None
        return channel
    except BaseException:
        if tls is not None:
            tls.close()
        if raw is not None:
            raw.close()
        raise


__all__ = [
    "PinnedRDPCredSSPChannel", "RDPBindingStatus", "RDPChannelError",
    "connect_verified_rdp_credssp", "read_tsrequest",
]
