# RDP / SuperAgent — synchronous multi-task hardening batch (2026-11-22)

All numbered items are code changes in this ONE stacked branch, not separate
PRs or claims of end-to-end interoperability. The batch is based on
#371 → #370 → #369 → #367 → #359 → #358 → #357 → #356 → #353.
The tasks were performed in a coordinated, serial GitHub update pass.

## Completed implementation checklist

1. Use a dedicated RDP credential reader instead of the SSH username parser.
2. Preserve backward compatibility with owner-selected host/username/password JSON.
3. Accept explicit Windows `domain` JSON field.
4. Parse `DOMAIN\\user` into domain and login without passing a slash as username.
5. Support `.\\localuser` for local Windows accounts.
6. Permit bounded Unicode Windows usernames and UPN-style names.
7. Require an exact literal node IP match inside the credential file.
8. Reject duplicate JSON credential keys.
9. Reject unexpected/ambiguous credential fields.
10. Reject control characters and invalid login formatting.
11. Validate username/domain/password field sizes before CredSSP.
12. Limit local credential JSON to 8 KiB and refuse oversized reads.
13. Refuse symlink credential files, including O_NOFOLLOW where available.
14. Recheck inode/device and regular-file type after opening.
15. Erase the mutable temporary byte buffer when done (immutable copies are not erasable in Python).
16. Bind RDP approval to the SHA-256 of the original SuperAgent owner task.
17. Bind approval to the original task creation timestamp.
18. Refuse retroactive remote access when ticket mandate was denied at creation.
19. Recheck original task, owner, node grant and kill switch throughout the approval lease.
20. Verify consent BEFORE any native TCP connection, in addition to later credential rechecks.
21. Reject non-finite/Boolean native RDP handshake timeouts without network access.
22. Add exactly one read-only cognitive worker per native SuperAgent session.
23. Wake that worker on genuine VLM observations without blocking the TLS/PNG callback.
24. Fail closed on untrusted/invalid LLM proposals without dispatching remote input.
25. Expose only bounded cognitive status counters and safe error types to the owner UI.
26. Retire the cognitive thread after native graphics naturally finishes or Stop.
27. Hide model proposals unless a fresh native framebuffer matches their update count.
28. Increase the bounded approved single-session distinct PNG budget to 256.
29. Abort outstanding Web image requests on Stop, hiding or closing the panel.
30. Revoke old PNG object URLs after both successful and failed image decode.
31. Remove visible RDP pixels when the browser tab is not active.
32. Ensure at most one polling timer per expanded RDP panel.
33. Avoid appending JSON after HTTP PNG headers/body have started.
34. Add mock and negative regression tests for credentials, replay, revocation,
    task mutation, timeouts, worker lifecycle and Web response boundaries.

## Verification scope

- GitHub commits and selected source read-back confirmed.
- The current Web JavaScript source was parsed successfully by V8 in Code Mode.
- Tests were **authored but not executed**; GitHub CI is currently configured
  for manual workflow_dispatch only, and no real Windows RDP service was used.
- Python import/compile, complete unit suite, TLS/NLA/MCS, actual Windows login,
  framebuffer decoding, responsiveness and user interface E2E are NOT yet
  verified and remain blocking criteria for merging into main.
- No GUI input/mouse/keyboard, model-generated authorization, automatic replay
  of credentials, Guacamole, Docker Compose or PostgreSQL proxy was added.
