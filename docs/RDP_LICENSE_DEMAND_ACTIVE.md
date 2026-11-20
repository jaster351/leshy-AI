# RDP licensing status / Demand Active intake (2026-11-21)

The internal native RDP transport now advances past its Client Info send to
**read and validate** two server messages over the SAME pinned TLS connection.

Protocol reference: MS-RDPBCGR sections 2.2.1.12, 2.2.1.13.1; MS-RDPELE
1.3.3. These are server PDUs, not instructions to install a license.

## Implemented

`src/leshy/rdp_server_activation.py` validates:

1. A canonical TPKT/X.224/MCS SendDataIndication frame (`0x68`), bounded
   to 16 KiB, with a valid PER length, correct I/O channel, and full,
   nonfragmented data (no truncated or trailing bytes).
2. An **unencrypted**, TLS-protected `SEC_LICENSE_PKT` `ERROR_ALERT`
   carrying `STATUS_VALID_CLIENT=7`, `ST_NO_TRANSITION=2`, and an
   empty `BB_ERROR_BLOB`. Per the specification, `flagsHi` is ignored
   when `SEC_FLAGSHI_VALID` is not set.
3. The subsequent server `TS_DEMAND_ACTIVE_PDU` with exact share-control
   length, version/type, source descriptor length, combined capability
   length, bounded count and individually bounded capabilities. Both
   General and Bitmap capability types must be present, and duplicate
   capability types are not silently accepted.

`PinnedRDPCredSSPChannel.receive_server_activation()` is single-use and
requires the previously completed, separately owner-approved CredSSP
credentials transfer, successful User/I-O MCS join and submitted Client Info.
It revalidates authorization and uses the channel's shared monotonic
deadline, and closes TLS/GSS on any decoding/authorization error.

The result is `RDPServerActivation`, whose `confirmed_active=False` and
`desktop_available=False` are intentional. **Receiving Demand Active is
not receiving an image and does not authorize input.**

## Scope and limitations

This implements **only the personal-terminal-server Valid Client licensing
flow**. Other legitimate RDPELE flows (new license, upgraded license, license
request/challenge, issued CAL, encrypted legacy security) are not implemented
and cause an explicit error. No license is forged, synthesized or persisted.
There is no automated retry, legacy downgrade, credential logging,
clipboard/drive forwarding, browser UI or untrusted model-facing action.

After Demand Active, the client must still exchange Confirm Active and
finalization PDUs, implement security/graphics capabilities, decode real
framebuffer updates and connect authenticated input and the existing UI.
A real Windows RDP Server interoperability/E2E test remains mandatory.

## Tests

- **24/24 local pytest checks passed**, for the independent codec and
  malformed MCS, license and capability envelopes, plus compileall.
- `tests/test_rdp_server_activation_channel.py` adds fake-TLS sequencing,
  owner revocation, gate, wrong channel and partial packet regressions.
  These additional tests are committed but are not yet run against a full
  local Leshy checkout.
- Both existing manual-only Linux and Windows RDP workflows now include
  both files; no GitHub CI run has been triggered here.

The pending security changes in draft PR #340 are separate; this feature
must not be confused with a completed real RDP desktop.

Microsoft:
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpele/32851b14-da8a-4a72-9fd4-41209bddd6e8
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/7d941d0d-d482-41c5-b728-538faa3efb31
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/bd612af5-cb54-43a2-9646-438bc3ecf5db
