# RDP Batch hardening (phase A): scoped credentials and launch

- Windows RDP credential JSON now has a dedicated, strict reader. Legacy
  host/username/password JSON remains supported, with optional domain,
  `DOMAIN\\user` or `.\\localuser`. Supports Unicode Windows login names
  without loosening the existing SSH parser. Limits: 8 KiB file, 128-character
  username, 128-character domain, 2,048-character password. Duplicate/extra
  keys, symlinks, inode changes, mismatched literal host, malformed Unicode,
  empty passwords and ambiguous domain forms fail closed. No secrets in repr.
- The one-use approval now additionally hashes and binds the actual original
  SuperAgent owner request and creation timestamp. Changing the ticket task
  burns a prepared token and invalidates an already-issued delegation lease.
  A ticket explicitly denied remote access at creation cannot be retroactively
  upgraded by logging in later.
- The owner consent callback is checked BEFORE any native RDP TCP connection
  and again before loading credentials and inside CredSSP. Invalid NaN,
  Infinity or Boolean timeouts are rejected before all network access.
- Still no automatic reconnect after any uncertain credential delegation.
- Changes rely on the dependent PRs #353/#356/#357/#358/#359/#367/#369/#370/#371.

## Phase B: active model consumption, Web privacy and lifecycle

The native vision observation inbox now has exactly one supervised local
Planner LLM worker. The real VLM -> observation callback only wakes the
worker; it never performs inference in the RDP/TLS callback. Stop wakes and
revokes the worker without waiting on LLM I/O. Late or superseded model
proposals never issue tools or clicks. A malformed model result stops
the read-only session and clears the Web image. The UI shows count and
error **type**, not raw prompt or model response.

Web PNG fetches use AbortController, cancel on Stop/panel close, release
old object URLs on successful and failed image decoding, suppress aborted
error banners, and clear screen contents while the browser is hidden.
Only one polling timer per expanded panel remains active. If server
headers are already sent, socket failure cannot append a JSON body.
The native stream stays bounded at 256 distinct PNG frames maximum per
single explicitly approved connection; there is still no reconnect.

No mouse or keyboard control has been enabled. No real Windows RDP E2E
is claimed; a secure, owned lab server remains necessary.
