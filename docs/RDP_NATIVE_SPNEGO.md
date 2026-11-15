# Native SPNEGO for CredSSP (Windows / Linux)

New modules:
- `src/leshy/native_spnego.py` — system GSSAPI (POSIX), target validation and OS selection.
- `src/leshy/native_spnego_windows.py` — Windows `Secur32.dll` SSPI `Negotiate`.
- `src/leshy/rdp_credssp_negotiator.py` — owner-guarded CredSSP token and TLS public-key binding state machine, with a **separately approved, internal-only** credential-handoff stage.

## What is actually implemented

The native SPNEGO engines use OS credentials only: Windows acquires the
current logon identity through `AcquireCredentialsHandleW` and constructs a
`TERMSRV/<dns-host>` SSPI context. Linux uses system MIT/Heimdal GSSAPI with
an existing Kerberos ticket, imports `TERMSRV@<dns-host>` as a host service
and explicitly selects the SPNEGO mechanism OID 1.3.6.1.5.5.2.

They request **mutual authentication, confidentiality, integrity, replay
detection and sequencing**, without delegation. If the provider lacks any
mandatory property, authentication fails. `wrap` and `unwrap` rely on the
native authenticated security context, reject non-confidential results and
enforce size limits. Each stage invokes a required server-side owner
authorization callback. Hostnames must be DNS names for Kerberos SPN; the
existing RDP TCP/TLS transport may still connect to an enrolled literal IP,
but the service's authenticated DNS identity must be verified independently.

The CredSSP negotiator exchanges v5/v6 `TSRequest.negoTokens`, requires a
previously trusted SHA-256 RDP TLS certificate and the certificate obtained
from the current TLS connection, uses the random nonce and public-key binding
from PR #230, transmits **only GSS-encrypted `pubKeyAuth`**, and verifies the
peer's authenticated GSS-unwrapped server binding before marking
`server_bound=True`.

The generic TSRequest codec still forbids inbound `authInfo`. A dedicated,
internal, separately approved step can now send GSS-encrypted `authInfo` after
a successful, independently pinned server binding. This is **not** available
to the LLM, Telegram or the public Web UI.
Successful server binding is not equivalent to RDP logon. These components
are private Python APIs and must not be exposed to untrusted clients, Web,
Telegram or a model-generated action with an unsafe authorizer.

## What remains unavailable

The existing TLS preflight disconnects after certificate checking; it does
not hand out a persistent encrypted socket. Therefore these SPNEGO classes
are **not wired to a real RDP server** yet. A full implementation still needs
further verification of real Windows credential acceptance, RDP MCS/channel
negotiation, graphics and input. The long-lived pinned TLS channel and
credential encoder are internal components; full desktop interop remains untested.

On Linux, the OS must have a GSSAPI shared library and a valid Kerberos ticket
for `TERMSRV` service authentication; these cannot be silently synthesized.
No extra daemon or Docker stack is installed. Windows uses the system SSPI
provider and does not require an RDP gateway.

## Verification and limitations

- Linux GSSAPI was exercised locally with the system `libgssapi_krb5`;
  without Kerberos tickets the library correctly rejected the negotiation.
- Local isolated tests verified service-target validation, platform-ABI
  structures, token length limits, owner denial, no-credentials failure,
  CredSSP state transitions, premature server binding and revocation.
- **Windows SSPI handshake, Kerberos/NTLM server interoperability, native
  GSS Wrap/Unwrap against an actual remote host, and Windows RDP E2E remain
  untested**. Treat the Windows adapter as experimental until those checks.
- The native Windows and Linux providers have no external Python dependency,
  but availability of the system Kerberos/GSSAPI library on every Linux
  distribution is not guaranteed.

References:
- [Microsoft InitializeSecurityContext](https://learn.microsoft.com/en-us/windows/win32/api/sspi/nf-sspi-initializesecuritycontextw)
- [Microsoft Negotiate EncryptMessage](https://learn.microsoft.com/en-us/windows/win32/secauthn/encryptmessage--negotiate)
- [GSS-API RFC 2744](https://www.rfc-editor.org/rfc/rfc2744)
- [MS-CSSP CredSSP](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cssp/)


## Windows SSPI decrypted-buffer hardening (2026-11-15)

The SSPI `DecryptMessage` provider can adjust the pointer of the decrypted
`SECBUFFER_DATA` inside its caller-allocated input buffer. The Windows
adapter now validates the decrypted pointer, its length, its buffer type and
the owning allocation range *before* copying bytes. It reads from the
provider-returned address rather than blindly taking a prefix of the original
ciphertext allocation. Any invalid offset, empty data or unexpected QOP
(including `SECQOP_WRAP_NO_ENCRYPT`) fails closed and terminates the
context. This does not establish remote interoperability.

Mocked SSPI ctypes regression tests are in
`tests/test_native_spnego_windows_buffers.py`, runnable without a Windows
RDP host. Python RDP pytest tests are now explicitly included in the existing
`.github/workflows/ci.yml` manual workflow. A separate
`.github/workflows/rdp-windows-manual.yml` runs analogous checks on a
Windows runner when manually dispatched. GitHub Actions remain
**manual-only until the repository's existing 2026-12-01 limit date**:
no automatic pull-request or push triggers were enabled.
