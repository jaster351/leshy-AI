# Native RDP explicit Web approval and one-use launch

Stacks on #370 → #369 → #367 → #359 → #358 → #357 → #356 → #353.

The existing SuperAgent Tasks panel now supports a two-phase, owner-only
review of an RDP launch. It selects an already registered node and an
owner-scoped local credential JSON basename, then asks the owner to
independently obtain/verify the RDP certificate SHA-256 and DNS TERMSRV
service name. This server does not fetch a certificate to fill its own
trusted pin, and the registered SSH host fingerprint is never used as
the RDP certificate fingerprint.

POST /v1/superagent/rdp/start/prepare:
- Requires provider-verified, live HttpOnly Web session, current
  remote-admin switch, X-Leshy-UI, same-origin and JSON.
- Validates working ticket owned by actor, registered node/rdp capability,
  exact local credential filename without reading password, DNS name
  and owner-provided independently confirmed TLS SHA-256.
- Stores only a hash of an unpredictable random token, bound to
  owner+live Web session fingerprint+ticket+node+DNS+TLS SHA-256+file.
  Memory bounded to 128 approvals, TTL 90s; no DB, disk or logger.

POST /v1/superagent/rdp/start/execute:
- Requires all exact fields plus token and confirm:true. Consumes token
  atomically BEFORE network or credentials. Concurrent replay fails closed.
- Rechecks authorization, target/credential selector/owner ticket and
  provides a 75s live owner-approval lease ONLY to the in-process
  launcher for that specific run. It cannot be used to start another
  connection. Each password-delegation recheck checks owner/session/grant.
- Runs the existing background TLS pin→CredSSP→delegated password→MCS
  sequence once. Connection errors after password send are not replayed.
- The RDP status and Stop controls already implemented show real
  protocol phases, error type and frames. LLM cannot mint an approval,
  send input or see secret contents.

Caveats: the credential JSON format is the existing SSH owner-scoped
host/username/password format and may not support all Windows login name
formats. TERMSRV DNS/Kerberos environment requirements are separate
from TLS pin verification and may prevent login. There is still no
confirmed Windows RDP E2E; native mouse/keyboard remain disabled.

The first real deployment needs explicit test-machine enrollment and
independent review. No tests or Windows E2E run for this draft.
