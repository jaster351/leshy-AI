# RDP session controller — transport-independent contract

`src/leshy/superagent_rdp_session.py` introduces a small, **not yet wired**
RDP lifecycle controller designed for future Windows/Linux backends.

- Only a caller-supplied, server-side authorization callback may admit the
  verified owner and enrolled node. Never build it from a user-supplied ID.
- The backend must perform **RDP TLS verification** against an independently
  enrolled SHA-256 certificate fingerprint before returning a connection;
  **SSH host-key pinning does not verify an RDP certificate**.
- Each screenshot generates a SHA-256 receipt. A click requires the most
  recent receipt, consumes it once and expires after five seconds.
- Coordinates are limited to the observed framebuffer.
- RDP session IDs are random and owner-bound. A revoked owner cannot capture
  or click, but can still close an existing session.
- Frame content and authentication secrets are not stored or logged.
- Sessions are process-local and limited to four by default.

This is a **backend interface and safety contract only**, not an RDP
implementation. No background RDP process is started; there is no user-facing
API or tool registration. `autonomous_rdp_available` must remain false.

Next implementation stages: native encrypted framebuffer/input backend with
certificate validation; cancel/timeout and teardown on worker termination;
durable receipt semantics and explicit authorization integration; real
Windows/Linux E2E tests; then SuperAgent UI. Do not use Guacamole, Docker
or mandatory target-host agents.

Mock tests cover permitted click, stale frames, replay, wrong owner,
revoked permissions and capacity. They do not test real RDP sessions.

## Frame delivery and bounded input

`observe_frame(owner_id, session_id)` returns an in-process `(FrameReceipt, pixels)`
pair for an authenticated vision adapter. The raw bytes come from the
backend's declared pixel format; do not assume PNG or send them to a public
endpoint. A 32 MiB size limit prevents unbounded frames. Bytes are never
persisted or included in audit logs.

Receipts have a random nonce in their digest. Even pixel-identical successive
screens produce different receipts, so actions prepared against a previous
observation are rejected.

`type_text(..., frame_digest=..., text=...)` permits 1–512 printable
characters. It uses the exact same owner checks, frame-age bound and one-use
receipt as `click`. Control characters and arbitrary keyboard shortcuts are
not accepted. A transport must explicitly implement `type_text`; otherwise
the operation fails without dispatch.

These methods do NOT implement RDP networking, frame decoding, keyboard scan
codes or a language-model vision adapter. No new RDP permissions or API
routes have been granted.
