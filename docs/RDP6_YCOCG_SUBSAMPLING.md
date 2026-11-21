# RDP 6.0 AYCoCg Color Loss and Chroma Subsampling (2026-11-21)

This is an incremental **real decoder** change after PR #347. The native
RDP graphics path still does not prove that a Windows desktop can be
displayed or controlled end to end.

## Implemented

`src/leshy/rdp6_planar.py` now understands all documented RDP6
`FormatHeader` combinations except invalid combinations/reserved bits:

- `CLL=0`: existing lossless RGB/ARGB, exact top-down RGB24 output.
- `CLL=1…7`: lossy AYCoCg color-space recovery following
  MS-RDPEGDI 3.1.9.1.2 and 3.1.9.1.4.
- Correct signed chroma-byte reconstruction: first left-shift Co/Cg by
  `CLL-1` **modulo 8-bit**, then interpret as signed 8-bit. For a pixel,
  `T=Y-Cg`, `R=clamp(T-Co)`, `G=clamp(Y+Cg)`,
  `B=clamp(T+Co)`. These equations match FreeRDP's portable
  `prim_YCoCg.c` reference.
- Optional CS flag with `CLL>0`: Co/Cg planes have
  `ceil(width/2) × ceil(height/2)` pixels. Each chroma sample expands
  to a 2×2 region, including odd-width and odd-height edges.
  Luma/alpha remain full resolution.
- Both RAW planar encoding and independent per-plane RDP6 scanline RLE
  are supported, including the separately sized subsampled Co/Cg
  planes. Source bottom-up rows are converted to top-down RGB only after
  all color planes are validated.
- Alpha values other than fully opaque are still rejected; these require
  separate transparency compositing. RGB CS with CLL=0 is illegal.
  Unsupported/truncated/reserved streams remain fail-closed.
- The existing approved pinned-TLS channel, `parse_bitmap_update()`,
  and transactional framebuffer consume the returned RGB through
  the same path. No separate service, credentials, Web route,
  model-facing tool, or remote input was added.

### Caution: known Microsoft server color-channel quirk

MS-RDPEGDI notes that a Microsoft server converting an **original
24-bpp RGB source** to YCoCg can invert the red and blue components.
This cannot be inferred from an opaque compressed bitmap simply because
the output `TS_BITMAP_DATA` is 32 bpp. This stage does **not**
blindly swap red/blue; interoperability must determine the source
condition or verify real server fixtures before adding a conditional swap.

## Evidence

- Local Python 3.13 `pytest`: **67 passed** for the existing RDP6
  lossless codec tests (36), new AYCoCg/CLL/subsampling tests (17)
  and full binary TPKT/X224/MCS/Bitmap Update tests (14).
- Includes 100 seeded odd/even valid AYCoCg streams, signed chroma edge
  cases, extended color-loss shifts, correctly sequenced RLE planes,
  malformed headers, incomplete Co/Cg, and the old ARGB/RGB regression
  vectors.
- GitHub code blob SHA for the decoder matches the exact local
  tested byte content; the new `test_rdp6_ycocg.py` blob also matches.
- Both existing Windows/Linux manually dispatched workflows now list
  the new test module. **Full GitHub Actions and real Windows Server
  RDP NLA/graphics E2E have not run.**

## Still missing for a usable desktop

Server interoperability/real RDP screenshots, long-lived receive loop,
bitmap clipping/fast-path updates, resizing, visible Web/Telegram UI,
Stop/reconnect, and independently validated authorization/one-time
input receipts (draft PR #340). The implementation must not claim that
an RDP desktop, keyboard or mouse is ready yet.

Normative sources:
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegdi/9b422f69-8e05-4c6d-b6fb-fa02ef75a8f2
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegdi/ef530a0a-03d8-482f-989b-57a1036797b2
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegdi/57507b94-41e6-41ff-a35e-c00211945ceb
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegdi/b4462e64-2286-4e77-9c5e-2046fc490cc0
- https://github.com/FreeRDP/FreeRDP/blob/master/libfreerdp/primitives/prim_YCoCg.c
