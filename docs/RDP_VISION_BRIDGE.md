# Native RDP → PNG vision bridge (read-only)

The trusted in-process entry point
\`leshy.rdp_vision_bridge.stream_native_rdp_png(channel, on_png,
reauthorize=..., policy=..., max_png_frames=...)\` consumes **the existing**
\`PinnedRDPCredSSPChannel.stream_bitmap_frames\` receiver on the same
independently pinned TLS/NLA/MCS session. It does not build a new session or
delegate credentials. No Guacamole, guacd, Docker, additional service,
screen scraping, HTTP route, Telegram route or OS-specific helper is added.

## What happens

1. A trusted backend must already have a provider-authenticated owner,
   registered IP + service DNS identity, separately pinned RDP server TLS
   certificate, approved credential delegation, accepted MCS/licensing,
   Confirm Active and finalization. The bridge cannot bypass this state:
   \`stream_bitmap_frames\` rejects premature access.
2. Verify the owner/session/node/RDP grant with the caller's \`reauthorize\`
   callback before streaming and before each image. The underlying channel
   rechecks owner grants during all TLS reads.
3. Reject partial, invalid, improperly sized, out-of-order and
   input-enabled frames. Encode **complete decoded RGB24** frames to
   standards-compliant PNG in memory (≤32 MiB input, ≤36 MiB PNG).
4. Send \`RDPVisionPNG\` only to the explicitly trusted in-process callback.
   Duplicate RGB content is omitted using only its SHA-256 digest; no image
   data or credentials are written to persistent storage or logs.
5. Owner Stop or delivery quota asks the existing channel to stop.
   All exits close the TLS/GSS channel, including validation, permission,
   encoder and consumer errors. No automatic reconnect or replay.

\`RDPVisionPNG.input_enabled=False\` is intentionally not an input receipt.
The content digest is for duplicate suppression and diagnostics **inside the
trusted process**, never as an authorization token. Do not send PNG bytes or
session identifiers to public endpoints or logs.

## Integration still required

A trusted owner-scoped runtime service must supply the real connected
\`PinnedRDPCredSSPChannel\` and an authorized, nonblocking vision/UI
consumer. There is still no exposed HTTP/Telegram/LLM tool, confirmed
Windows Server E2E capture, complete fast-path support, real remote
mouse/keyboard or input approval flow. Code-level mocked regressions are
not evidence of real Windows RDP interoperability. PR #340 and #352 cover
separate receipt and timeout concerns; do not merge their changes blindly.
