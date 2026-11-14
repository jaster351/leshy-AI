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
