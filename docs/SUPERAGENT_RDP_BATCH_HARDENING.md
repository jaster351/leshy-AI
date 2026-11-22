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
