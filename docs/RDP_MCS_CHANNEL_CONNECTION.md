# Native RDP MCS Channel Connection (Erect / Attach / Join)

This stage follows `docs/RDP_MCS_BASIC_SETTINGS.md` and uses the *same*
owner-verified pinned TLS socket. It is **not a working desktop client**.

## Wire exchange

Source: `src/leshy/rdp_mcs_channels.py`.

After successful MCS Connect-Response on the same TLS socket:

1. Send MCS Erect Domain Request, setting subHeight/subInterval to zero
   (TPKT hex `03 00 00 0c 02 f0 80 04 01 00 01 00`).
2. Send MCS Attach User Request
   (TPKT hex `03 00 00 08 02 f0 80 28`).
3. Read **exactly** one Attach User Confirm, require PER success and an
   encoded User ID based on MCS channel base 1001.
4. Send MCS Channel Join Request for the resulting **user channel**.
   Read its success confirmation before continuing.
5. Send MCS Channel Join Request for the previously negotiated **I/O channel**
   from the GCC Server Network Data. Read its separate success confirmation.

No static virtual channels were advertised in Client Network Data, so no
clipboard, drive, printer, sound or arbitrary extra channel is joined.

For example, a server assigning user channel 1007 (PER offset 6) and I/O
channel 1003 yields client join requests:
`03 00 00 0c 02 f0 80 38 00 06 03 ef` and
`03 00 00 0c 02 f0 80 38 00 06 03 eb`.
Server confirmation for I/O channel 1003 must be
`03 00 00 0f 02 f0 80 3e 00 00 06 03 eb 03 eb`.

## Authorization / trust boundaries

`PinnedRDPCredSSPChannel.join_mcs_channels()` is an **internal-only**
operation, gated by successful MCS Basic Settings and one-time separately
approved CredSSP credential delivery. It reauthorizes the verified owner
before and after each transmission, enforces the existing handshake deadline
and consumes the attempt even when aborted.

Every server response must have an exact TPKT length, X.224 Data TPDU,
expected PER PDU type and successful result. The user ID, requested channel
and confirmed channel must match, preventing out-of-order, wrong-channel or
duplicate responses. Any error closes the entire TLS/GSS context.
The return type `MCSJoinedChannels` explicitly says
`desktop_available=False`.

The implementation never bypasses TLS pinning, never sends arbitrary
keyboard/mouse events, and does not connect to any Web, Telegram or LLM route.

## Tests and remaining steps

22 local codec pytest cases passed, with independent boundary checks for
user-channel IDs 1001 and 65535. Separate simulated TLS integration tests
cover exact send/receive ordering, owner revocation, bad confirm, missing
server settings and disabled credentials. The integration tests are checked
into `tests/test_rdp_mcs_channel_join.py` and scheduled for the
**manual-only** Windows/Linux CI workflows; they have not yet been run in
GitHub Actions or on an actual Windows RDP server.

More RDP protocol remains: RDP Security Commencement / Secure Settings,
Client Info, licensing and Demand Active/Confirm Active capabilities,
connection finalization, framebuffer updates, compression and input.
Even with both MCS joins completed, **no logged-in desktop or autonomous
RDP control is yet verified**. No Guacamole, Docker or third-party daemon
has been introduced.

Normative references:
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/023f1e69-cfe8-4ee6-9ee0-7e759fb4e4ee
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/04c60697-0d9a-4afd-a0cd-2cc133151a9c
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/3b3d850b-99b1-4a9a-852b-1eb2da5024e5
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/7443e65e-3482-4cec-823a-741eb1fe27b4
