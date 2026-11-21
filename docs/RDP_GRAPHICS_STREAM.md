# Owner-bounded native RDP graphics receive loop (2026-11-21)

After PR #348, Leshy's native RDP decoder can recover the common 16/24/32
bit Bitmap Update variants and assemble a complete RGB framebuffer.
Previously, the post-handshake receive path still used the original short
CredSSP/MCS handshake deadline, so a graphical session could not receive
updates for longer than that initial timeout.

## Implemented

The in-process `PinnedRDPCredSSPChannel.stream_bitmap_frames(on_frame,
policy=...)` uses the **same pinned TLS socket, verified CredSSP credentials
and finalized MCS session**. It does not start any worker/daemon itself; the
trusted owner caller can execute it on its own thread. The existing
`stop_graphics_stream()` sets a thread-safe cancellation event.

- Separate, bounded **graphics session lifetime** (default 300 seconds,
  allowed 1–3600), **per-packet idle deadline** (default 60 seconds,
  allowed 0.5–600), and total update limit (default 2048, max 10,000).
  They do not loosen the original short CredSSP handshake deadline; a
  streaming session must begin while that handshake is still valid.
- `rdp_graphics_stream.read_graphics_tpkt()` reads one exact TPKT
  (16–16384 bytes) with <=200 ms socket polling. It checks the explicit
  stop event and reruns the independent enrolled owner/node authorization
  **between every fragmented TLS read**, and again before returning bytes.
  Truncation, oversized records, malformed framing, unauthorized access,
  or a packet exceeding its deadline all fail closed.
- The receive loop validates each packet with the existing
  `parse_bitmap_update()` (real 16/24/32bpp RGB/RLE/planar decoder),
  then applies the complete batch transactionally with
  `RDPBitmapCompositor`. A frame callback is invoked **only** when every
  pixel has actually been received; repeated partial regions cannot
  fabricate a screen. Permission and Stop are checked again immediately
  before callback invocation.
- Publishes immutable `RDPComposedFrame` values with
  `input_enabled=False`. Optional minimum publication interval (default
  100ms) protects slow consumers from excessive image callbacks;
  decoded-but-not-published updates still advance framebuffer state.
- Normal Stop or update/duration limit returns a metadata-only
  `GraphicsStreamResult` containing packet count, published frame count,
  and completion reason. **All exits, including successful ones, close the
  existing TLS/GSS connection and reset its local framebuffer coverage.**
  Interrupted TLS packets are never resumed/reused in another session.
- The synchronous `on_frame` consumer must be **trusted, nonblocking and
  owner-scoped**. Callback errors are not suppressed: they close the channel.
  No screenshot route, HTTP/Telegram endpoint, mouse, keyboard or model
  tool has been added.

## Verification

- Local Python 3.13: **21/21 unit tests passed** for the exact
  `src/leshy/rdp_graphics_stream.py` and
  `tests/test_rdp_graphics_stream.py` blobs. Cases include two separate
  TPKT frames on a single stream, fragmented TLS, temporary socket
  timeouts, Stop before receiving, mid-packet permission revocation,
  invalid headers, oversized packets, expired deadline, finite policy
  bounds and malformed EOF.
- `tests/test_rdp_graphics_stream_channel.py` adds simulated in-process
  TLS+framebuffer tests for two-row full coverage, repeated partial rows,
  Stop during a packet and from a callback, revoked ownership, missing
  CredSSP/finalization, truncated image, and sink failure. Both existing
  Linux and Windows manual RDP CI workflows include the new test files.
- **Full GitHub Actions/Windows E2E have not run.** A correct codec and
  receiver do not prove that a Windows RDP desktop can actually be reached,
  drawn or controlled. No `VERSION` bump is justified yet.

## Still outstanding

- Real Windows Server RDP/NLA end-to-end and actual screen captures.
- Unsupported fast-path output updates, clipped rectangles, drawing orders,
  dynamic resizing, and RDP non-bitmap management messages. These currently
  terminate the read-only stream rather than pretending to support them.
- User-visible Web UI rendering and Stop UI integration; no connected
  presentation service has been implemented.
- Real owner-controlled mouse and keyboard, stale-frame protection and
  authorization receipts. **Draft PR #340 remains unmerged** and must be
  independently validated before remote input is enabled.
- Keep a trusted consumer nonblocking: this code cannot interrupt an
  arbitrarily blocked callback; it bounds network reads and checks Stop
  between records.

References:
- Microsoft MS-RDPBCGR Slow-Path Graphics Update:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/3523c4c5-80aa-4e63-9c43-a94c4494aa79
- Microsoft MS-RDPBCGR Server Fast-Path Update PDU:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/68b5ee54-d0d5-4d65-8d81-e1c4025f7597
- Python SSLSocket receive and timeout semantics:
  https://docs.python.org/3/library/ssl.html
