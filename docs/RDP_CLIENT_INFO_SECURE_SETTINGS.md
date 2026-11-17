# RDP Client Info / Secure Settings Exchange (no additional password)

This stage follows `docs/RDP_MCS_CHANNEL_CONNECTION.md`. It is internal
protocol work, **not** evidence that the Windows user logon has completed.

## Protocol

The new `src/leshy/rdp_client_info.py` builds a bounded MS-RDPBCGR
`Client Info PDU` over the established, pinned, owner-verified TLS channel:

- TPKT + X.224 Data TPDU + PER `MCS SendDataRequest`.
- Initiator = negotiated MCS User Channel ID minus 1001.
- Destination = negotiated MCS I/O Channel ID (not a static virtual channel).
- Security header: `SEC_INFO_PKT=0x0040`, `flagsHi=0`. TLS already
  provides encryption and integrity, so **legacy SEC_ENCRYPT is not set**.
- `TS_INFO_PACKET`: `INFO_UNICODE`, `INFO_LOGONNOTIFY`,
  `INFO_NOAUDIOPLAYBACK`. Language ID `0x0409` matches the existing
  client keyboard layout.
- `Domain`, `UserName`, `Password`, `AlternateShell`, and
  `WorkingDir` are all **empty UTF-16LE strings with required terminators**.
  Their length fields are zero (length excludes terminating NUL).
  No `INFO_AUTOLOGON`, no PIN, no arbitrary executable path or shell.
- Minimal `TS_EXTENDED_INFO_PACKET`: validated literal client IPv4/IPv6
  address, its address-family constant and generic non-sensitive client
  directory `C:\\`. Neither workstation files nor real installation paths
  are exposed. No timezone, auto-reconnect cookie, device redirects or
  graphics capabilities are promised.
- TPKT length and PER userData length are computed from actual payload bytes.
  Unknown or out-of-range channel identifiers and nonliteral, scoped,
  unspecified or multicast client IP addresses are rejected.

## Connection gate

`PinnedRDPCredSSPChannel.send_client_info(client_address=...)` is a
**one-shot internal-only** operation. It is permitted only after:

1. Owner-approved, pinned TLS and authenticated CredSSP credentials delivery.
2. Successful MCS Basic Settings and both User/I-O Channel Join confirms.
3. Revalidation of the owner's node access permission immediately before and
   after sending the packet.

Failure, revocation or partial TLS delivery closes the socket; there is
no blind retry, and no Web/Telegram/LLM endpoint exposes this method.
The client address must come from trusted local socket metadata rather than
an untrusted external request.

The return receipt explicitly says `windows_logon_verified=False` and
`desktop_available=False`. **Sending Client Info cannot prove that a Windows
session accepted credentials.**

## Testing

- `tests/test_rdp_client_info.py`: 15/15 local pytest checks passed;
  exact Python source and test file hashes match the branch Git blobs.
- `tests/test_rdp_client_info_channel.py`: additional simulated TLS
  integration tests cover owner revocation, repeated sends, no MCS join,
  invalid address, and partially failed sends. These are committed, but not
  yet run in a full assembled Leshy runtime.
- Both existing Windows/Linux RDP GitHub workflows include these test
  files, retaining manual-only `workflow_dispatch` triggers.
- No real Windows Server CredSSP logon, licensing, Demand Active, framebuffer
  or mouse/keyboard E2E has been verified.

Next phases: server licensing negotiation, Demand Active/Confirm Active,
session finalization, framebuffer updates, and securely authorized input.

References:
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/772d618e-b7d6-4cd0-b735-fa08af558f9d
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/86313d86-377e-4bab-97cb-c541dd9370ea
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/732394f5-e2b5-4ac5-8a0a-35345386b0d1
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/05ada9e4-a468-494b-8694-eb806a0ecc89
