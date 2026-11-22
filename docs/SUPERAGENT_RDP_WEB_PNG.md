# RDP Web real native PNG transfer — no synthetic screenshots

This PR stacks on #367 → #359 → #358 → #357 → #356 → #353.

The ONLY source of pixels is the already owner-authorized, pinned/native
RDP graphics channel decoded by stream_native_rdp_png. The bounded
local-vision worker accepts each real RDPVisionPNG and (optionally)
forwards it to one in-process callback. No HTTP request or LLM can
forge, upload, decode, or initiate its own frame. A coalescing
single-frame buffer replaces previous pixels in RAM. No disk storage,
history, screenshot logging, Knowledge, Telegram or cluster replication.

Web transfer:
- GET /v1/superagent/rdp/frame?ticket_id=... requires a provider-linked
  live Web cookie and the SuperAgent custom X-Leshy-UI header.
- The same exact browser session, owner ticket, node capability,
  registered fingerprint, live SuperAgent state, remote-admin switch
  and authorized frame are rechecked at the read/output boundary.
- A bounded native image (<=8 MiB, width/height checked by the
  existing image validator) is transmitted as image/png with nosniff,
  private/no-store and no-cache headers; no image URL is stored.
- Missing/stale/disconnected images return HTTP 204, NEVER fake pixels.
- The Web UI performs credentialed fetch and turns the validated
  response into a temporary object URL; the URL is revoked on image
  load/error, panel closing, Stop and changes to authorization.
- At most one frame poll runs at once, and the status check is
  limited to an expanded, visible Tasks panel. The UI never issues
  keyboard/mouse commands.
- Stop and binary transmission serialize at the registry lock;
  binary writes use a 3-second socket deadline so Stop cannot be
  blocked indefinitely by a slow browser. In-flight bytes already
  transmitted before Stop cannot be retracted from the network.
- The latest frame expires after 8 seconds and is cleared on native
  graphics completion, Stop, revoked authorization, or loss of stream.

Known limitations:
- A real, pinned, authenticated and finalized Windows RDP channel must
  already exist; this PR does NOT create one, and no E2E compatibility
  with Windows has been demonstrated.
- Existing max_png_frames limits native capture; this does not
  add reconnection, session persistence or screen-sharing gateway.
- Pointer and keyboard actions and owner action approval are NOT
  implemented. LLM suggestions remain review-only.
- Regression tests are supplied; no tests/build/Windows E2E were run.
