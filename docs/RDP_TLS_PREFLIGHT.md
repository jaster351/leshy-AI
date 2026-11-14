# Read-only native RDP TLS preflight

The first RDP networking stage lives in `src/leshy/rdp_tls_preflight.py`.

It uses Python's standard library on Windows and Linux; no Docker, Guacamole,
RDP gateway, background service, or remote-host agent is started.

## What it actually does

1. Requires a **server-verified authorization callback** for an enrolled node
   and RDP capability before accessing the network.
2. Requires a **previously trusted** 64-digit SHA-256 hash of the RDP server's
   DER-encoded TLS certificate. It must come from an independent trusted
   enrollment step; do not use trust-on-first-use or reuse an SSH host-key hash.
3. Connects to a **literal IP address** on TCP port **3389**.
4. Sends an X.224 Connection Request with `RDP_NEG_REQ` advertising TLS,
   CredSSP and CredSSP + Early User Authorization Result.
5. Parses the X.224 Connection Confirm and rejects unknown security modes,
   Standard RDP Security downgrades and malformed responses.
6. Applies one overall deadline to the TCP connection, X.224 reads and TLS\n   handshake, preventing indefinite negotiation by a trickle-feed server.\n7. Performs TLS 1.2+ handshake with an RDP certificate pin. RDP commonly uses
   self-signed certificates, so certificate **pinning is mandatory**, not
   optional: the TLS peer certificate's DER SHA-256 must match the independently
   trusted fingerprint before a successful result is returned.
8. Rechecks owner authorization and closes the encrypted socket. No password,
   NLA/CredSSP token, mouse event, image, clipboard data or remote command
   is transmitted.

The result explicitly reports `authenticated_desktop=False` because this
preflight does **not** log in or establish a framebuffer session. A successful
response only demonstrates that a server at the allowed address negotiated
an approved TLS security mode and presented the expected certificate.

Do not attach `probe_rdp_tls` directly to a Web route, Telegram command, or
LLM tool until the provider-verified owner enrollment, an independent RDP TLS
pin store, per-node authorization and rate limits have been integrated.
The current RDP session controller is still an unattached contract and
`autonomous_rdp_available` must stay `false`.

## Tests and limits

Seventeen isolated Python tests use fragmented mock TCP responses and mocked TLS for downgrade,
invalid packets, missing fingerprints, certificate mismatch, overall timeout and authorization
revocation. A separate local experiment used a synthetic X.224 responder
and a real TLS 1.3 handshake on loopback; **no real Windows or Linux RDP
server has been tested**.

Later development must implement native CredSSP/NLA authentication, RDP
graphics decoding and input transport, connection cancellation, persistent
enrollment policy, and an in-process visual agent. None of those features
are provided by this module.

Protocol sources:
- [MS-RDPBCGR RDP_NEG_REQ](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/902b090b-9cb3-4efc-92bf-ee13373371e3)
- [MS-RDPBCGR RDP_NEG_RSP](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/b2975bdc-6d56-49ee-9c57-f2ff3a0b6817)
- [MS-RDPBCGR X.224 request](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/e78db616-689f-4b8a-8a99-525f7a433ee2)
