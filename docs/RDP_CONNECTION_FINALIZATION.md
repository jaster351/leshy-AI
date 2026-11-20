# Native RDP Connection Finalization (2026-11-21)

This implementation follows Confirm Active in the owner-scoped native RDP
transport. Its existence does **not** mean that Leshy can display or control
a real Windows desktop.

## Implemented

- `src/leshy/rdp_finalization.py`: byte-exact outbound TPKT/X.224/MCS
  SendDataRequest + TS_SHAREDATAHEADER for, in this strict order,
  Client Synchronize, Client Control (Cooperate), Client Control (Request
  Control), and Client Font List.
- No legacy Security Header (TLS is already authenticated). No fast-path,
  clipboard, disk, audio, screenshot or input messages.
- No Persistent Key List: Leshy has no persisted bitmap cache or stored keys;
  the implementation never fabricates keys.
- Four inbound SendDataIndication replies must match the complete,
  length-bounded TLS/MCS envelope, server source, share ID, pduType,
  compression policy and expected stage in this order:
  Server Synchronize, Server Control (Cooperate), Server Control (Granted),
  Server Font Map. A Grant must identify the current **user channel ID**
  and server control ID `1002`; a Font Map must report exactly the
  supported zero-font profile.
- The server Synchronize targetUser is not used as authentication proof:
  Microsoft documents uninitialized values on some server versions.
- `PinnedRDPCredSSPChannel.finalize_mcs_connection()` can run exactly
  once, only after owner-approved TLS/CredSSP, MCS Join, Client Info,
  license/Demand Active and Confirm Active on the **same pinned TLS socket**.
  Each write and each read rechecks the owner-granted access and the existing
  total deadline. Errors and interrupted writes close the socket with no
  blind retry.
- Return `RDPFinalizationStatus` reports validated protocol control
  acknowledgements while always keeping `desktop_available=False` and
  `input_enabled=False`. No user-facing Web/Telegram/LLM RDP endpoint added.

## Verification

- 38 isolated tests passed locally with the exact GitHub blob SHA of
  `src/leshy/rdp_finalization.py` and exact committed test file.
  The local copy includes the same unmodified MCS indication parser as
  `rdp_server_activation.py` in main.
- `tests/test_rdp_finalization_channel.py` adds mocked TLS integration
  tests for owner revocation, wrong stage, truncated frames and the ordered
  four messages. Added to existing *manual-only* Linux and Windows workflows.
- Full Windows CI/real RDP E2E was **not** run during this change.
  Completing a protocol handshake does not show pixels and must not be
  advertised as an available desktop.

## Work still missing

Real Windows RDP login verification, stable long-lived receiving loop,
graphics capability interoperability, framebuffer updates and actual bitmap
decoding, handling dynamic screen changes, authenticated UI/Telegram display,
user-confirmed mouse and keyboard, Stop/disconnect, and end-to-end negative
tests on a real authorized server. Draft security PR #340 is still separate.

## Sources

- MS-RDPBCGR Connection Sequence, section 1.3.1.1:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/023f1e69-cfe8-4ee6-9ee0-7e759fb4e4ee
- MS-RDPBCGR Synchronize:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/3fb4c95e-ad2d-43d1-a46f-5bd49418da49
- MS-RDPBCGR Control PDU:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/0448f397-aa11-455d-81b1-f1265085239d
- MS-RDPBCGR Font List:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/7067da0d-e318-4464-88e8-b11509cf0bd9
- MS-RDPBCGR Font Map:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/7ba6ba81-e4f4-46a7-9062-2d57a821be26
