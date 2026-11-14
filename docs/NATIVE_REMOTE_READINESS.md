# Native SSH/RDP readiness (Windows and Linux)

The OS status payload now contains `remote_clients`, a local-only discovery snapshot.
No network connection, password collection, remote enrollment, or install happens during detection.

| Field | Meaning |
| --- | --- |
| `ssh_client_available` | The system CLI `ssh` executable was discovered on PATH |
| `rdp_launcher_available` | A native/previously installed RDP launcher was discovered on PATH |
| `ssh_client`, `rdp_launcher` | Basename only, never the absolute installation path |
| `autonomous_rdp_available` | Always false until an authenticated framebuffer/input transport is integrated and verified |
| `rdp_status` | Distinguishes human-operated launcher from absent launcher |

Windows probes `ssh.exe`, `mstsc.exe`, `msrdc.exe`. Unix-like systems probe
`ssh` and optional installed `xfreerdp`, `xfreerdp3`, `wlfreerdp`, `rdesktop`.
Detection is not a claim that RDP credentials, server certificates or sessions
will work. System PATH lookup does not determine SSH server availability.

Existing verified SSH execution stays independent of the system `ssh` CLI;
it uses the dedicated authenticated transport and owner approvals. Existing
`.rdp` profile download launches a human client, not AI screen control.

## Remaining work for AI-controlled RDP
- Authenticate the registered owner and target independently, including
  certificate validation for RDP (SSH fingerprints are not RDP identities).
- Establish an encrypted, managed session with a framebuffer and explicit
  keyboard/mouse actions; avoid running a sidecar on the destination.
- Bind each input action to the active session and recent framebuffer, provide
  cancel, timeouts and interaction limits, and do not log frame content.
- Connect controlled actions to SuperAgent ticket approvals and receipts.
- Verify on actual Windows and Linux hosts. Do not claim this feature is ready
  based only on executable discovery.

No Guacamole, Docker Compose, PostgreSQL or system package installation is required.
