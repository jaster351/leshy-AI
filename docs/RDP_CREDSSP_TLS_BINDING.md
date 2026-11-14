# CredSSP v5+ TLS public-key binding for native RDP

Module: `src/leshy/rdp_credssp_binding.py`.

This is the cryptographic binding stage described by
[Microsoft MS-CSSP §3.1.5](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cssp/385a7489-d46b-464c-b224-f7340e308a5c).

## Implemented

- Parse a bounded, DER-encoded X.509 leaf certificate and extract **only the
  SubjectPublicKey BIT STRING payload**, not the full DER certificate or the
  entire SubjectPublicKeyInfo. This distinction matters on strict Windows RDP
  servers.
- Verify the complete DER certificate's SHA-256 against a separately,
  independently enrolled owner-trusted RDP TLS certificate fingerprint.
  The SSH host-key fingerprint is **not** a substitute.
- Generate a new cryptographically random 32-byte client nonce.
- Compute version 5/6 client-to-server binding
  `SHA256(b"CredSSP Client-To-Server Binding Hash\\0" + nonce + subjectPublicKey)`.
- Compute the distinct server-to-client binding
  `SHA256(b"CredSSP Server-To-Client Binding Hash\\0" + nonce + subjectPublicKey)`.
  The magic strings are ASCII bytes and **include the terminal NUL byte**.
- Constant-time comparison of a **previously SPNEGO-unwrapped**, authenticated
  server binding hash with the expected server-to-client digest.
- Reject certificate pin mismatch, malformed/oversized/noncanonical DER,
  invalid BIT STRINGs, missing keys and invalid nonce size.

The only required library is Python's standard library; the test suite uses
an **optional** `cryptography` package to create genuine signed RSA/ECDSA
certificates. Tests requiring that package skip when it is not installed.

## Security boundary / remaining work

These functions are **cryptographic primitives, not a live RDP login**.
A caller MUST get the certificate from the current negotiated TLS channel,
check an independently enrolled pin, then obtain the authentication
mechanism's integrity-checked GSS/SPNEGO `Wrap/Unwrap` implementation.

**Never send the raw 32-byte client hash in `pubKeyAuth`**: MS-CSSP
requires it to be encrypted/sealed through the authenticated SPNEGO
session. Never mark server authentication successful based on a raw
`TSRequest.pubKeyAuth` token: first perform authenticated GSS unsealing.
The receiving caller, not this helper, must enforce correct GSS
confidentiality, token sequence numbers, and owner authorization.

`TSRequest.authInfo` remains disabled in the existing DER codec;
no password, username, PIN, or credential delegation is handled here.
No Web, Telegram, LLM or desktop control route is enabled. The native
RDP transport is not finished, and `autonomous_rdp_available` stays false.

## Validation

- 16 unit tests on the exact GitHub file blobs, including fixed independent
  SHA-256 test vectors and altered certificate/nonce rejection.
- Two additional signed X.509 certificate integration cases (RSA-2048 and
  ECDSA P-256) passed locally; they are optional when `cryptography` is
  unavailable.
- Local result: **18 passed**, Python syntax compiled.
- No real Windows RDP server or CredSSP authenticated session tested.

Next major step: implement OS-native SPNEGO/SSPI or a self-contained GSS
mechanism with verified confidentiality and public-key binding, then
CredSSP/NLA full handshake, RDP MCS/graphics and input.
