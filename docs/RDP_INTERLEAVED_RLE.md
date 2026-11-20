# Native RDP 16/24-bit Interleaved RLE bitmap decoder (2026-11-21)

The previous native RDP code (PR #344/#345) decoded only **uncompressed**
Bitmap Updates and assembled complete-coverage RGB snapshots. This change
implements **real Interleaved RLE bitmap pixel decompression** for
16- and 24-bit compressed `TS_BITMAP_DATA` messages, per Microsoft
MS-RDPBCGR 2.2.9.1.1.3.1.2.2–.4 and 3.1.9.

## Implemented

- `src/leshy/rdp_interleaved_rle.py`: bounded standard-library pixel
  decoder for regular, lite, extended/MEGA_MEGA and special orders:
  background/foreground runs, set-foreground, color runs, literal
  color images, alternating/dithered pairs, FG/BG bitmask images,
  special FG/BG masks, black and white.
- Correct bottom-up scanline processing, previous-scanline XOR/background,
  initial white foreground, back-to-back background insertion exception at
  the first scanline boundary, LSB-first bitmasks and extended run lengths.
  Pixels returned as strictly sized top-down RGB24 bytes.
- Hard limits: exact source consumption, complete pixel coverage of the
  *individual decoded rectangle*, max 16 KiB compressed input, 8 MiB
  decompressed RGB, dimensions <=4096 each, explicit rejection of unknown
  opcodes, zero/overflow runs and truncated data.
- `src/leshy/rdp_bitmap_update.py`: support for `BITMAP_COMPRESSION=0x0001`
  at bpp16/24, both with and without the optional 8-byte
  `TS_CD_HEADER` (`NO_BITMAP_COMPRESSION_HDR=0x0400`). When present,
  validate its first-row size, compressed body length, 4-byte scan width
  alignment and uncompressed output bound. Reject malformed flag mixtures.
- The previously implemented, owner-approved `PinnedRDPCredSSPChannel`
  and transactionally assembled `RDPBitmapCompositor` automatically use
  this decoder through their existing `parse_bitmap_update` call. No new
  credentials or login mechanisms, model-facing action, UI input, or new
  daemon were introduced. Any invalid compressed bitmap still causes the
  existing TLS/GSS channel to fail closed; no partial rectangle is returned.

## Tested

- **77 local pytest cases passed** under Python 3.13: 59 decoder cases
  (including 1000 seeded random malformed order streams in one case) and
  18 complete binary TPKT/X224/MCS/Bitmap Update test cases for
  standard/no compressed headers, RGB565, multi-rectangles, malformed
  sizes/flags, RDP6 refusal and truncated packets.
- The local tested decoder and updated Bitmap Update parser are identical
  to the GitHub branch blobs by SHA-1. The packet tests used a local copy
  of the existing Leshy MCS parsing routine; they are reproducible in
  `tests/test_rdp_interleaved_rle.py` and
  `tests/test_rdp_bitmap_rle_wire.py`.
- Both existing manually triggered RDP workflows on Linux and Windows
  include those two test modules. **No real Windows server RDP E2E or full
  GitHub Actions suite was run** during this change.

## Important limits / next work

- **32-bit compressed bitmaps are not Interleaved RLE**; they require
  RDP 6.0 planar bitmap decompression (MS-RDPEGDI 2.2.2.5.1) and are
  still rejected. Do not substitute zlib/MPPC.
- Real Windows may use clipped bitmaps, fast-path PDUs, large updates,
  alternate drawing orders and long-lived graphics streams; those are
  not yet supported in the main RDP transport. Existing bounded parser
  rejects unsupported flows instead of fabricating output.
- The end-to-end Windows NLA/SSPI session and actual framebuffer display
  in Web/Telegram are not yet verified. Mouse/keyboard must remain
  disabled until independent authorization/receipt checks, including
  separate draft security PR #340, are validated.

### Protocol references

- [MS-RDPBCGR Bitmap Data](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/84a3d4d2-5523-4e49-9a48-33952c559485)
- [MS-RDPBCGR RLE Compressed Bitmap Stream](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/b3b60873-16a8-4cbc-8aaa-5f0a93083280)
- [MS-RDPBCGR Interleaved RLE decompression pseudocode](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/b6a3f5c2-0804-4c10-9d25-a321720fd23e)
- [MS-RDPBCGR Compressed Data Header](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/3d1ccc49-51e2-4a2c-b300-3da9fb931d0e)
