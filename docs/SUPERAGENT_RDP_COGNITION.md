# SuperAgent native RDP cognitive session — trusted internal API

This work stacks on PR #358 (read-only proposals), which stacks on
#357 (coalescing vision worker), #356 (local VLM) and #353 (PNG).
It exposes an **in-process** factory
SuperAgentService.create_rdp_cognitive_session(ticket_id, actor,
node_id, observed_fingerprint) to tie a verified SuperAgent ticket
to a registered remote node and the existing cognitive RDP pipeline.

Requirements:
- Server-provided VerifiedActor, with a live provider-linked Web session
  checked by RemotePermissionPolicy._check_actor on every read and result.
- A registered remote node, observed registered fingerprint, and
  RemotePermissionPolicy.require(..., capability="rdp") per operation.
  This node enrollment fingerprint is NOT a substitute for the exact
  independent native RDP TLS certificate pin on the transport.
- Existing owner-scoped SuperAgent ticket in state working, with a
  bounded original owner mandate. Ticket Stop, disabled service,
  changed mandate or revoked grant deny any subsequent observation.
- The same existing SuperAgentService._chat_inference serializes work
  through the established LLM lock. The Planner object has NO tools
  and is used only for its existing real LLM; Planner.run is not called.
- accept_observation() is a fast internal handoff to the one-slot inbox.
  reason_once() must run from a trusted worker, NOT HTTP or TLS callback.
- receive_graphics() runs the previously implemented RDP/VLM pipeline
  from a trusted coordinator. It requires an already finalized native
  channel; it does not open a socket, enroll nodes or grant credentials.
- stop() revokes future responses and requests native TLS stream Stop.
  Model inference in flight cannot be forcefully interrupted; its
  result is discarded after permission revocation or Stop.
- Status contains only metadata; no image, model replies or secrets.
  Proposals are NEVER verified action receipts or executable input.

This does not create an HTTP/Telegram route, schedule RDP from a ticket,
produce a real mouse event, or prove interoperability with a Windows
RDP server. Native RDP input needs a separate owner approval gate,
fresh framebuffer receipt, and independent end-to-end testing.

Review and merge in dependency order before connecting to user UI.
