# Native RDP MCS/GCC Basic Settings Exchange

Next implementation stage after the owner-approved pinned TLS/CredSSP channel.

## Implemented

- `src/leshy/rdp_mcs_basic.py`: deterministic BER/PER codec for the RDP
  **MCS Connect-Initial** PDU, including X.224 Data TPDU and RFC-1006 TPKT.
  GCC Conference Create Request carries client core, security and network
  blocks, with the mandatory client H.221 identifier `Duca`.
- Client core uses bounded screen dimensions, 24-bit color depth and echoes
  the server-selected CredSSP/HYBRID-EX security protocol in
  `serverSelectedProtocol` as specified by MS-RDPBCGR.
- Client security block advertises **no legacy RC4/RDP encryption** because
  all post-handshake traffic stays inside the authenticated TLS channel.
- Client network block advertises **zero static virtual channels**:
  clipboard, drive, printer and audio redirection remain unavailable.
- Strict parser for MCS Connect-Response and server GCC H.221 key `McDn`.
  Validates TPKT/X.224/BER/PER framing, lengths, GCC block uniqueness,
  successful server result, domain version, encrypted transport requirements,
  protocol echo and I/O channel metadata. Rejects unsolicited static
  channels and any legacy RDP encryption parameters.
- `PinnedRDPCredSSPChannel.exchange_mcs_basic_settings()` sends this packet
  on the **same verified TLS socket** only *after* an explicitly approved
  CredSSP credential handoff was sent. It re-checks owner authorization,
  enforces the existing session deadline, reads one bounded MCS response and
  closes the session on any failure. The exchange is strictly single-use.
- If the server negotiated **CredSSP-EX (0x08)**, the client first reads the
  four-byte Early User Authorization Result and requires `AUTHZ_SUCCESS=0`;
  `AUTHZ_ACCESS_DENIED=5`, unknown values and truncated results fail closed.

## Not yet implemented

MCS Basic Settings Exchange is not RDP login success and does not expose a
desktop framebuffer or input. The server's settings response contains an I/O
channel number but the user channel is **not yet attached**. Next comes MCS
Erect Domain / Attach User / Channel Join, client info/capabilities, graphics
updates and authenticated input transport. Full Windows Server interoperability
and real NLA login still require end-to-end testing.

These are internal modules only: no Web/Telegram/LLM endpoint is connected.
There is no Guacamole, Docker, additional daemon or new runtime dependency.
The existing `autonomous_rdp_available=False` flag must remain false.

## Validation

- Local codec reference implementation: 20/20 pytest cases passed on Linux
  for client packet structure, strict server response parsing and malformed
  requests. Source code was then incorporated into the GitHub branch.
- New separate channel tests cover a successfully completed prior CredSSP
  transfer, denied transfer, early user authorization, timeout/fragments,
  owner revocation and single-use semantics. They are included in both
  manually triggered RDP CI workflows, without changing their schedules.
- The new channel tests, full CI suites, a real Windows SSPI handshake and a
  real Windows RDP host have **not** been run in this change.

References:
- [MS-RDPBCGR Client MCS Connect Initial](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/c1bea8bd-069c-4437-9769-db5d27935225)
- [MS-RDPBCGR example client packet](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/2610fcc7-3df4-4166-85bb-2c7ae21f6151)
- [MS-RDPBCGR example server response](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/d23f7725-876c-48d4-9e41-8288896a19d3)
- [MS-RDPBCGR Early User Authorization Result](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/d0e560a3-25cb-4563-8bdc-6c4cc625bbfc)
