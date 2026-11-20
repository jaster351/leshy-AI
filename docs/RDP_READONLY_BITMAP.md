# Native RDP uncompressed Bitmap Update intake (21 November 2026)

This step follows `docs/RDP_CONNECTION_FINALIZATION.md`. It implements
**real decoding of one narrow type of server graphics data**, without
inventing a working Windows remote desktop or bypassing owner access.

## Implemented

`src/leshy/rdp_bitmap_update.py` decodes **one complete, slow-path,
uncompressed** Bitmap Update (MS-RDPBCGR 2.2.9.1.1.3.1.2):

- Verifies an exact, bounded TPKT / X.224 / MCS SendDataIndication envelope.
  Its source user, destination I/O channel and Share Data share ID must match
  the previously authenticated and finalized session.
- Requires Share Control `PDUTYPE_DATAPDU`, Share Data
  `PDUTYPE2_UPDATE`, `UPDATETYPE_BITMAP=1`, no bulk compression,
  a consistent uncompressed PDU length and bounded rectangle count.
- Parses length-counted `TS_BITMAP_DATA` records. Rectangle right/bottom
  bounds are **inclusive**; screen coordinates must fit the negotiated
  desktop dimensions.
- Accepts *only* uncompressed 16-bit RGB565, 24-bit BGR and 32-bit BGRX
  pixels, converts scanlines from bottom-up to top-down **RGB24** and strips
  the mandatory four-byte scanline alignment padding. Pixel data is never
  interpreted as UTF-8 or logged.
- Rejects unknown depths, bitmap flags, compressed data, clipped dimensions,
  invalid strides, oversized images, missing rectangles, truncated data,
  duplicate/trailing bytes or unexpected PDUs. No fallback is used.
- Returns **actual decoded rectangles** (`left`, `top`, `width`,
  `height`, `rgb`) without manufacturing black pixels for portions of
  the screen that have not yet arrived.

`PinnedRDPCredSSPChannel.receive_bitmap_rectangles()` accepts one such
server message only **after** owner-approved CredSSP credentials, valid
MCS/Client Info/licensing/Confirm Active and all four finalization replies.
Each network operation is owner-reauthorized under the existing deadline.
Any malformed, compressed or unexpected update closes TLS/GSS; successful
calls can read successive rectangle packets, but they do not authorize
mouse/keyboard or create a full-frame receipt. No Web/Telegram/LLM endpoint
has been added. No screenshot is passed to a model.

## Tests

- **30/30 local unit cases passed** for the new decoder. The exact
  `rdp_bitmap_update.py` and `test_rdp_bitmap_update.py` bytes were
  uploaded to GitHub and checked by SHA; the local run used a small
  self-contained copy of the existing MCS envelope parser to avoid external
  network access. This does NOT establish that the full Leshy test suite ran.
- `tests/test_rdp_bitmap_channel.py` covers simulated TLS fragmented
  reads, owner revocation, missing finalization, compressed formats,
  correct RGB pixels and sequential updates. Included in manually triggered
  Windows/Linux RDP workflow manifests. These integration tests and real
  Windows RDP end-to-end tests have **not** been executed.

## Important limitations

A typical Windows RDP server may use **Interleaved RLE** for compressed
16/24-bpp Bitmap Updates or RDP 6.0 bitmap compression for 32-bpp updates.
Both require separate, validated codecs and are currently **unsupported**.
The parser also rejects clipped rectangles with mismatched source dimensions
rather than making an unsafe assumption about clipping.

There is **no full framebuffer compositor, coverage validation, long-lived
graphics receive loop, display UI, real RDP screenshot, resize/DPI handling
or remote input**. The current handshake-wide deadline is still used by
this read path; connection persistence must be designed and validated before
claiming production graphics streaming. The owner-gated framebuffer action
controller remains independent; **draft security PR #340 is not merged**.
Version is not increased for this partial internal feature.

Next priorities: implement safely bounded Interleaved RLE/RDP 6.0 decoding
against external captures or Microsoft fixtures, add a surface compositor
that does not publish incomplete or stale frames, then full Windows NLA /
real server interoperability tests before enabling the UI or clicks.

Microsoft specification:
- Bitmap Update Data:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/d681bb11-f3b5-4add-b092-19fe7075f9e3
- Bitmap Data, scanlines and compression formats:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/84a3d4d2-5523-4e49-9a48-33952c559485
- Slow-path graphics:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/bd3c4df4-87b9-43dd-88cb-ce5b24698e19
