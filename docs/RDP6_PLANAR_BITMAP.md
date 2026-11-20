# RDP 6.0 planar bitmap decoding (21 November 2026)

This is a bounded part of the native RDP graphics receive path in Leshy,
following Interleaved RLE (#346). It is **not** a finished remote desktop.

## Implemented: lossless RGB / ARGB subset

- `src/leshy/rdp6_planar.py`: validates the one-byte `RDP6_BITMAP_STREAM`
  FormatHeader, the color-plane layout (optional Alpha, Red, Green, Blue)
  and exact pixel coverage. Supports uncompressed planes and RDP 6.0
  per-plane scanline RLE, including RAW/RUN segments, 16+ and 32+ extended
  runs and per-scanline signed delta reconstruction.
- Rejects reserved format bits, non-RGB/YCoCg color modes (CLL != 0),
  chroma subsampling, malformed segments, scanline crossing, unexpected
  trailing bytes, truncated planes and oversized data. Nonopaque alpha
  is explicitly unsupported; dropping transparency silently would be unsafe.
- The bitmap decoder is wired into `src/leshy/rdp_bitmap_update.py`
  **only when bitsPerPixel == 32 and BITMAP_COMPRESSION is set**.
  It handles both the optional 8-byte Compressed Data Header and the
  `NO_BITMAP_COMPRESSION_HDR` flag, retains strict TPKT/X224/MCS/share
  verification and returns the same verified RGB rectangles as other codecs.
- The TS_BITMAP_DATA source is bottom-up; rows are reversed only after
  plane decoding for the existing top-down RGB framebuffer consumer.
  This matches the orientation used by FreeRDP's bitmap decompression
  call with vertical flipping on the normal bitmap path.
- Limits: <=16 KiB compressed stream, <=8 MiB decoded RGB rectangle,
  width/height <=4096; no unbounded allocations or out-of-frame writes.
- Existing owner-approved pinned TLS channel, authorization checks and
  transactional RGB compositor consume the output without any new API,
  credentials or mouse/keyboard permissions.

## Verification, not yet Windows E2E

- Locally executed `pytest -q`: **36 codec tests passed**, including the
  published Microsoft six-column/three-scanline RLE delta example, raw
  ARGB/RGB, extended runs, bottom-up orientation, truncated data and
  400 seeded bounded random streams. The tested `rdp6_planar.py` matches
  its GitHub blob SHA-1 exactly (`ae6f7b0d9635e37500ece2a9fd40a7ecf584cd8e`).
- `tests/test_rdp6_bitmap_wire.py` adds binary TPKT/MCS/Bitmap Update
  examples with/without the compression header, planar raw and RLE,
  malformed flags/lengths and color modes. These tests have been added
  to existing manual-only Windows and Linux RDP CI workflows.
  A full repository test run and real Windows RDP interoperability
  have **not** been performed as part of this PR.
- The connected RDP UI, persistent graphics receiver and approved
  mouse/keyboard are still **not available**. Draft security PR #340
  remains separate; it must be validated before enabling input.

## Remaining compression/graphics work

RDP6 also permits lossy AYCoCg conversion with Color Loss Level 1-7 and
optional chroma subsampling, and nonopaque alpha. These variants are
currently rejected instead of misrendering them. Other unresolved
work: fast-path graphics, clipped bitmap updates, graphics-stream lifetime
beyond the handshake deadline, dynamic sizing, screenshot presentation
and real Windows NLA end-to-end tests. No Guacamole, Docker or auxiliary
remote-control server has been introduced.

Microsoft references:
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegdi/9b422f69-8e05-4c6d-b6fb-fa02ef75a8f2
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegdi/f7e4c717-669e-4f31-8b34-e8f5ab2e107e
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegdi/46a9972c-0cdd-4673-add4-87f89b837742
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/84a3d4d2-5523-4e49-9a48-33952c559485
