# Owner-approved native RDP launch — internal, no public start endpoint

Stacks on #369 → #367 → #359 → #358 → #357 → #356 → #353.
Reuses the already implemented native pinned CredSSP/MCS/RDP channel,
rather than a simulated screenshot or external Guacamole/sidecar.

SuperAgentService.start_authorized_native_rdp is a **trusted in-process**
entry point, NOT a Web, Telegram, tool-registry or model-accessible route.
It requires:
- Verified provider-linked Web actor and live owner-scoped working ticket
- Registered node with current rdp capability, independent exact RDP TLS
  SHA-256 pin enrolled **outside the connection**, and approved TERMSRV
  DNS service name separate from the literal registered IP
- Owner-selected, local JSON credential filename from the existing guarded
  owner credential directory (never in request payload or model context)
- Independent callable authorize_delegation that checks existing human
  consent to *this exact node, TLS identity and credential delegation*;
  it is not minted by this module and is rechecked before reading the
  password and immediately prior to the one-way CredSSP credential send.

After checking existing grants and owner ticket, the worker attempts ONE
pinned TCP/TLS handshake, CredSSP server binding, a separately authorized
sealed password delegation, MCS settings, channel joins, Client Info using
the literal address observed on its own socket, licensing/Demand Active,
Confirm Active, finalization and finally read-only PNG graphics.

Native startup phase/status and error class are shown in the existing
SuperAgent RDP panel. Stop can also close the pinned socket during
handshake, not only once desktop pixels arrive. The RDP image buffer is
cleared on Stop/closure and no tool is allowed to issue OS mouse/keyboard.

The attempt never retries after a possibly sent credential or uncertain
remote state. No connection is opened by GET, and no user-configured
service can bypass explicit grant + human approval. Failure information
contains only exception class, not protocol bytes, username, password,
file paths, or tokens.

Important: this does NOT itself provide a public start button, establish
Windows E2E compatibility, demonstrate a real successful Windows logon,
or implement native input. The next step is to implement a one-use,
scope-bound owner approval flow and an authenticated start route, then
test against an actual explicitly enrolled Windows RDP service.
No unit/CI/Windows E2E tests have been executed for this draft.
