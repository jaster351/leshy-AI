# Native RDP transactional RGB framebuffer (2026-11-21)

Follows `docs/RDP_READONLY_BITMAP.md` (PR #344). This feature assembles
**real, decoded, uncompressed RDP Bitmap Update rectangles**, but does
**not** imply a working live Windows desktop. No Docker, Guacamole,
third-party gateway, additional process, or Web/Telegram/LLM route is used.

## Data path

`PinnedRDPCredSSPChannel.receive_bitmap_frame()`:

1. Requires the existing owner-approved, pinned TLS/CredSSP channel to have
   completed MCS / Client Info / licensing / Confirm Active / finalization.
2. Serializes reads so two callers cannot interleave TPKT fragments.
3. Uses the existing `receive_bitmap_rectangles()`, which validates the
   server identity, protocol framing, share and I/O channel, and returns
   *decoded* RGB rectangles only.
4. Applies the returned rectangles to an `RDPBitmapCompositor` owned by
   the same connection. The compositor stores **coverage** for every pixel
   separately from its RGB byte buffer and refuses to publish a frame
   while coverage is incomplete.
5. Returns `None` until every pixel has genuinely arrived; then returns
   an immutable `RDPComposedFrame` with RGB24 bytes, dimensions and update
   count. No forged black image, stitched previous session, UI action
   receipt, mouse, keyboard or auto-logon is created.
6. Revalidates owner permission after receiving and after compositing.
   On any error or permission revocation, TLS/GSS closes and local
   compositor coverage is reset.

`src/leshy/rdp_bitmap_compositor.py` bounds the RGB surface to **32 MiB**,
limits each update to 64 already-decoded rectangles and each rectangle to
8 MiB, rejects malformed pixel lengths and out-of-bounds coordinates,
and applies each entire batch transactionally. Overlapping patches do
not count twice towards initial coverage. After complete coverage,
subsequent valid patches update only the received regions; previous
immutable frame objects remain unchanged. `reset()` revokes coverage
for new/re-activated sessions. No complete frame can be produced until
the surface is fully initialized from valid pixels.

## Verification

- Local Python 3.13: **60/60 tests passed** running the PR #344 bitmap
  decoder's existing 30 tests, 26 new compositor tests, and 4 new tests
  that parse real byte-structured MCS/Bitmap Update packets and then
  assemble the RGB surface. The local test environment used an exact
  copy of the prior protocol modules; this is not a full-repository run.
- Exact Git blob hashes of the new compositor and its local tests match
  the files in the GitHub branch.
- `tests/test_rdp_bitmap_compositor_channel.py` adds independently
  constructed fragmented-TLS simulation tests: a complete frame only
  after two updates, duplicate-packet coverage, missing NLA/finalization,
  unsupported compression, revocation between packets, truncated TLS
  frame, and reset on close. Added to the manual Windows/Linux RDP
  test workflows.
- Full repository and Windows GitHub Actions tests have **not** run;
  no actual Windows RDP server has been connected and no real screen
  captured. No version bump.

## Remaining blockers

**Compressed Bitmap Updates are still unsupported.** Windows often
transmits Interleaved RLE (16/24bpp) or RDP 6.0 bitmap compression
(32bpp); both require specification-conformant codecs and real captured
test vectors. A standard zlib inflate is not a correct substitute.
Also missing: clipped bitmap rectangles, general graphical orders,
a durable graphics receive loop (current global handshake deadline
still applies), server resizing, full Windows NLA E2E, real display UI,
stop/reconnect behavior and owner-controlled remote input.

Draft security PR #340 remains separate and must be validated before
enabling any remote mouse/keyboard action. A composed RGB frame is
**not** an approved click authorization receipt.

Microsoft references:
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/84a3d4d2-5523-4e49-9a48-33952c559485
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/b6a3f5c2-0804-4c10-9d25-a321720fd23e
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/fdd7c4e9-a0f2-415d-af5e-e48389def356
