# Native RDP X.224 / pinned TLS preflight (Windows and Linux)

Modules:
- `src/leshy/rdp_tls_probe.py`: direct network X.224 connection negotiation and TLS peer certificate validation via Python standard library.
- `src/leshy/superagent_rdp_tls.py`: internal, owner-scoped entry point using the existing verified Web/provider session, enrolled node and per-node `rdp` grant via `_registered_pin`.

## Security and scope

The probe offers `PROTOCOL_SSL | PROTOCOL_HYBRID | PROTOCOL_HYBRID_EX` and **never** accepts a downgrade to legacy `PROTOCOL_RDP`. It requires TLS 1.2 or later and an *exact, independently confirmed SHA-256 fingerprint* of the remote RDP TLS certificate (the DER certificate, not the SSH server key).

Self-signed RDP certificates are accepted **only if** their exact independently provided TLS certificate fingerprint matches; the initial TLS context does not use public CA trust because the certificate pin is mandatory and verified before reporting success. Certificate mismatch is fatal. No password, username, CredSSP credentials or session token is ever transmitted. The connection is closed immediately after the TLS certificate check.

Targets must be IP literals (not arbitrary hostnames); timeout, port and protocol payload lengths are validated. The owner-scoped wrapper checks server-side RDP capability before opening any connection, then checks it again after the handshake.

**Do not derive the expected RDP TLS fingerprint from the response of this same untrusted connection.** The fingerprint must be verified independently by the owner, using a trusted administration channel or management console. Existing SSH host-key hashes do not authenticate the RDP TLS certificate.

## What this does not do

This is a genuine TCP/X.224/TLS client *preflight*, **not a complete RDP client**. It does not implement CredSSP/NLA, login, graphics or audio channels, RDP framebuffer decoding, keyboard/mouse events or an interactive remote desktop. It is intentionally not exposed as a direct public HTTP or LLM tool until a safe enrollment and approval flow is ready. `autonomous_rdp_available` remains `false`.

It runs on Python's standard library on Windows/Linux without Docker, Guacamole, any target-side agent or an installed remote-desktop client.

## Verification

`tests/test_rdp_tls_probe.py` exercises fragmented packet parsing, downgrade rejection, argument validation and a local mock RDP server that completes a real TLS handshake and validates an ephemeral pinned certificate. That network test generates a temporary one-day self-signed certificate with the OpenSSL **test utility** and skips when it is unavailable; OpenSSL is not a runtime dependency of the module.

`tests/test_superagent_rdp_tls.py` checks authorization before the first outbound connection and after the TLS handshake, including revocation.

Protocol references:
- Microsoft MS-RDPBCGR X.224 Connection Request: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/18a27ef9-6f9a-4501-b000-94b1fe3c2c10
- RDP Negotiation Request: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/902b090b-9cb3-4efc-92bf-ee13373371e3
- RDP Negotiation Response: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/b2975bdc-6d56-49ee-9c57-f2ff3a0b6817

Next essential phase: real CredSSP/NLA and protocol channel setup, then RDP graphics/input. Do not claim actual desktop access before real Windows/Linux E2E.
