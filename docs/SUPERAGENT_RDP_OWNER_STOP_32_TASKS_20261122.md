# Leshy SuperAgent native RDP — owner verification and Stop safety

**Coordinated batch of 32 changes and regression scenarios**, stacked on
PR #372 and its RDP dependencies. This document describes code that was
committed, not proof that an external Windows RDP server worked.

## Approval and authorization

1. Remove two actual NUL characters from the Python RDP approval source.
2. Add a regression that rejects NUL bytes in the approval Python module.
3. Detect an explicit RDP/remote-desktop *target* in an original user task.
4. Require an affirmative user action such as connect, open or inspect.
5. Deny generic mentions of RDP without a connection request.
6. Deny negated owner instructions including "не подключайся".
7. Deny tutorials and explanatory requests ("как", "что такое", "расскажи").
8. Track provider-verified remote authorization at task creation for RDP.
9. Require the source ticket to be active/working at the approval boundary.
10. Require creation-time remote authorization to be explicitly true.
11. Reject old, anonymous and missing-authority ticket mandates.
12. Preserve existing task text and creation timestamp consent binding.
13. Check a local credential JSON is a small regular file before approval.
14. Bind owner consent to its device/inode/size/modification/change stamp.
15. Check file identity again when consuming the one-use token.
16. Check file identity again before one-way CredSSP password delegation.
17. Reject symlinked or oversized files during consent preparation.
18. Add a same-owner/same-browser revoke API for unconsumed approvals.
19. Deny cross-browser revocation attempts.
20. Expose a cancel route with strict existing Web identity, origin and JSON checks.
21. Call cancellation when the owner rejects the explicit browser approval.
22. Never report "no remote connection" when the token has already been used.

## Native sessions and shutdown

23. Reserve the owner/ticket key while the previous RDP Stop runs.
24. Reject concurrent registration of a replacement in the Stop window.
25. Keep failed TLS Stop reservations blocked until service reset.
26. Close the actual active pinned TLS socket on Stop, not only set a flag.
27. Close the TLS channel even when its graceful graphics Stop fails.
28. Continue shutting down SuperAgent and releasing worker lease after RDP Stop errors.
29. Attempt Stop on *every* RDP session during shutdown, even when one fails.
30. Add concurrency and failure regression coverage for these lifecycles.

## Credentials, CI and usability

31. Reject JSON numeric/bool IP aliases and invalid Unicode surrogate halves
    before preparing a Windows CredSSP password packet; make symlink
    regressions portable to Windows machines without symlink privileges.
32. Add the 11 owner/RDP pytest suites, all-Python NUL scanner and native
    SuperAgent Web JavaScript syntax check to the EXISTING manual CI;
    explain creation-time permissions in the Web panel.

## Remaining acceptance gates

- Manual CI workflow_dispatch must be explicitly run and pass. No new
  automatic GitHub Actions cadence was introduced (repository policy).
- Actual Windows TLS/NLA/Kerberos/NTLM/MCS/framebuffer/Stop E2E not tested.
- No native mouse or keyboard actions, automatic credential retry,
  Guacamole, Docker Compose or model-generated owner permission.
- Before merge, review PR #340/#352/#368 plus all stacked dependencies
  #353→#356→#357→#358→#359→#367→#369→#370→#371→#372→this PR.
- Because existing deployed tickets cannot acquire retroactive RDP privileges,
  owners need a **new explicitly remote-desktop task**, created while the
  provider-linked Web session is valid.
