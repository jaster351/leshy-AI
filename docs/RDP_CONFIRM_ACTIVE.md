# Native RDP Confirm Active — experimental capability exchange

Continues native RDP after the previously implemented read-only server
Valid Client license + Demand Active (PR #341).

## Wire format

`src/leshy/rdp_confirm_active.py` implements bounded TPKT / X.224 /
PER MCS SendDataRequest / TS_CONFIRM_ACTIVE_PDU serialization using the
`shareId` received from the server Demand Active and the previously joined
user and I/O MCS channels.

- Client Share Control Header: `pduType=0x0013`, `pduSource=user channel ID`.
- `originatorID=0x03EA` (the server channel identifier).
- Length-counted `LESHY\0` descriptor and 7 sized capability sets:
  General, Bitmap, Control, Window Activation, Pointer, Share and Input.
- No legacy 4-byte security header: external TLS from pinned NLA/CredSSP
  already protects this frame. This **differs** from the Client Info and
  licensing PDUs, which do have their own Security Headers.
- General capability does not announce Fast-Path, bitmap acceleration,
  RemoteApp or auto-reconnect; Input advertises only protocol-mandatory
  scancode support (no implemented keyboard input service exposed).
- Bitmap capability advertises mandatory bitmap compression and multiple
  rectangle support *for protocol interoperability*. A bitmap decoder is
  **not** implemented: the channel cannot yet render incoming updates or
  accept input. Do not present this capability advertisement as working
  pixel/codec support.
- Server settings from Demand Active must include General and Bitmap types.
  Client bitmap dimensions reuse the dimensions requested in MCS Basic
  Settings. The on-wire depth is limited to 24 bpp by the channel entrypoint.
- Reject invalid share identifiers, channel IDs, screen sizes, color depth,
  missing mandatory capabilities and oversized PER/TPKT payloads.

## Internal transport integration

`PinnedRDPCredSSPChannel.send_confirm_active()` is single-use and requires:
an owner-approved CredSSP handoff; completed MCS Basic Settings; both User
and I/O Channel Joins; Client Info send; and successfully parsed Valid Client
licensing plus server Demand Active on the *same pinned TLS socket*. It
rechecks the owner grant before and after the send and enforces the session
deadline. Failed/uncertain sends close the socket without automatic retry.

The only returned receipt is `ConfirmActiveReceipt` with
`finalization_complete=False`, `desktop_available=False` and
`input_enabled=False`. No Web, Telegram or LLM entrypoint is connected.

## Validation and limits

- 24/24 local pytest cases passed against the **exact GitHub file blob SHA**
  for `src/leshy/rdp_confirm_active.py`; syntax check passed.
- The initial version had a 4-byte Input Capability length error; it was
  corrected before upload and all 24 cases were rerun successfully.
- Extra simulated TLS tests cover missing prerequisites, owner revocation,
  one-shot behavior and partial sends, and are included in both manual-only
  Windows/Linux CI workflows. These integrated tests have not been run
  against a full Leshy checkout or a real Windows Server RDP host.
- A successful Confirm Active *send* is **not** proof of acceptance by the
  Windows server. No Server Synchronize, Control, Font Map, bitmap decode
  or authorized keyboard/mouse transport is implemented yet.

**Next:** add client Synchronize, Cooperate, Request Control and Font List
with strict server-side acknowledgements, and real-image decoding before
marking RDP ready. Finish Windows NLA E2E before enabling user-facing access.
The draft security PR #340 remains separate; do not merge overlapping
changes without its outstanding checks.

Normative references:
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/4e9722c3-ad83-43f5-af5a-529f73d88b48
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/4c3c2710-0bf0-4c54-8e69-aff40ffcde66
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/76670547-e35c-4b95-a242-5729a21b83f6
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/023f1e69-cfe8-4ee6-9ee0-7e759fb4e4ee
