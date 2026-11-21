# SuperAgent RDP Web control plane (read-only, stacked)

This PR is stacked on #359, which depends on #358, #357, #356, #353.
It adds an actual owner-scoped status and Stop surface to the existing
SuperAgent Tasks panel, not an independent RDP client or UI.

- A trusted in-process SuperAgent factory registers its already verified
  SuperAgentRDPCognitiveSession in a bounded, ephemeral registry.
- GET /v1/superagent/rdp/live?ticket_id=... requires server-validated
  provider-linked Web cookie, owner ticket, exact matching browser session,
  current RDP node grant and live ticket state. Missing sessions return
  explicit no_live_session, not a fabricated RDP connection.
- POST /v1/superagent/rdp/live/stop requires the existing same-origin
  X-Leshy-UI header, JSON and confirm=true. It removes the session and
  requests stop of the native graphics callback. No creation or remote
  command is accepted from HTTP. Stop remains usable after RDP grant loss,
  provided the Web identity is still verified and owns the same session.
- SuperAgentService.stop_ticket() and stop() revoke matching/all active
  RDP sessions. The registry is process-local and does not survive restart;
  a newly started process cannot pretend to own an old RDP channel.
- The owner can see verified node, whether the existing native graphics
  receive loop is active, and the latest read-only model proposal.
  The observation is cleared on a newer image or Stop. Proposed coordinates
  are shown as text, never a clickable, executable approval.
- The panel explicitly states that Web screenshots and remote mouse/keyboard
  input are not yet available. It does not render a fake screenshot.
- No PNG pixels, remote credentials, raw TLS pin, RDP input token or shell
  command appears in the GET payload or diagnostic logs.

Still outstanding: trusted in-process startup of a real pinned and
finalized Windows RDP session, Web image transfer gated by actual framebuffer
availability, owner-approved native input, full compatibility testing and
merging all stacked PRs. This draft has no confirmed Windows RDP E2E.
