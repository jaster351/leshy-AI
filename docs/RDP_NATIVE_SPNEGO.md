# Native SPNEGO for CredSSP (Windows / Linux)

New modules:
- `src/leshy/native_spnego.py` — system GSSAPI (POSIX), target validation and OS selection.
- `src/leshy/native_spnego_windows.py` — Windows `Secur32.dll` SSPI `Negotiate`.
- `src/leshy/rdp_credssp_negotiator.py` — owner-guarded CredSSP token and TLS public-key binding state machine, **without password delegation**.

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

The TSRequest `authInfo` credentials field is **not sent or supported**.
Successful server binding is not equivalent to RDP logon. These components
are private Python APIs and must not be exposed to untrusted clients, Web,
Telegram or a model-generated action with an unsafe authorizer.

## What remains unavailable

The existing TLS preflight disconnects after certificate checking; it does
not hand out a persistent encrypted socket. Therefore these SPNEGO classes
are **not wired to a real RDP server** yet. A full implementation still needs
a long-lived pinned TLS transport and framed TSRequest exchange, additional
CredSSP completion checks, a deliberate owner-approved credential delivery
method where appropriate, RDP MCS/channel negotiation, graphics and input.

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
