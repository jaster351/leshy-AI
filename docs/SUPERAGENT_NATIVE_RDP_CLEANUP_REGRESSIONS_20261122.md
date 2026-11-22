# Native RDP cleanup: 28 coordinated behavior and regression checkpoints

Dependency chain: #375 → #374 → #373 → #372 → #371 → #370 → #369 → #367 → #359 → #358 → #357 → #356 → #353.

These checkpoints describe source changes and authored tests, NOT a passing Windows end-to-end run.

## Shutdown, privacy and failure reporting
1. Always attempt native TLS close after the worker.
2. Always clear pending handshake state even when close raises.
3. Preserve the first CredSSP/protocol error if transport close also fails.
4. Report a standalone transport close failure as a class only.
5. Never propagate raw exception messages to Web status.
6. Revoke the cached PNG upon native failure.
7. Clear the cached frame timestamp upon native failure.
8. Reset cached frame update sequence upon failure.
9. Revoke previous model proposals upon native failure.
10. Preserve the first failure class against later cleanup faults.
11. Convert non-string error types to a generic safe class.
12. Reject CRLF/token/private-path error message strings.
13. Reject non-ASCII and overly long error-class strings.
14. Ignore late native-failure reports after owner Stop.

## Owner-scoped Web status
15. Report failed native transport distinctly from waiting.
16. Report a finished graphics stream without claiming Windows E2E.
17. Never show connected=true during failed TLS teardown.
18. Never show graphics_active=true during failed TLS teardown.
19. Never show screen_available=true during failed TLS teardown.
20. Maintain input_enabled=false regardless of model output.
21. Maintain e2e_verified=false until a true authorized E2E check.

## Native transport and CI boundaries
22. Reject invalid or unsafe local JSON credential basenames.
23. Reject non-integer and out-of-range frame budgets.
24. Reject NaN/Inf and bounded-timeout violations before TCP.
25. Reject non-literal RDP IP node identifiers before TCP.
26. Reject invalid TCP ports and TLS pin values before TCP.
27. Recheck real owner consent before TCP and before credentials.
28. Include direct pinned TLS/CredSSP and loopback tests in the existing opt-in manual Windows CI.

## Verification limitations
Source readback and V8 JavaScript syntax checks performed. Python pytest and Windows GitHub Actions not run. No authorized Windows RDP E2E, TLS login, framebuffer or Stop interoperability has been confirmed. Main must remain unchanged until independent review and testing. No Gusli or Android endpoint changes here.
