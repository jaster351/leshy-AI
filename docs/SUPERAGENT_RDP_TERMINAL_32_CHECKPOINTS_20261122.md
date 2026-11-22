# SuperAgent native RDP: terminal-phase and Web race hardening

Stack: PR #376 → #375 → #374 → #373 → #372 → #371 → #370 and earlier RDP prerequisites.
This is a coordinated batch of **35 implementation and regression checkpoints**, not 32 separately benchmarked or released features.

## Native RDP and cognitive boundary
1. Reject late native PNG callbacks after a recorded TLS/NLA/MCS failure.
2. Reject PNG callbacks after graphics stream finished.
3. Reject PNG callbacks after owner Stop.
4. Recheck terminal state both before and after PNG validation.
5. Reject VLM observations after native failure.
6. Reject VLM observations after graphics finish.
7. Reject VLM observations after owner Stop.
8. Refuse new LLM inference on terminal RDP sessions.
9. Discard in-flight LLM output when native failure occurs during inference.
10. Wake and retire the dedicated cognitive worker when native failure is recorded.
11. Avoid incrementing completed analysis counters after failure or Stop.
12. Hide old read-only proposals after terminal session transitions.
13. Check terminal state when retrieving the current frame.
14. Repeat the frame state check after the live owner authorization recheck.
15. Block transition from failed native state back to graphics.
16. Block transition from finished native state back to graphics.
17. Revoke framebuffer, timestamp, frame count and proposals when graphics finish.
18. Ensure cognitive worker wakes after a finished stream.
19. Mask graphics_active and frame_available in native snapshot on terminal phase.
20. Mask stale frame_update in native snapshot on terminal phase.

## Owner-scoped Web status and binary delivery
21. Mask connected/active flags for failed, finished and stopped native phases.
22. Mask proposal count/visibility if native graphics are not healthy.
23. Mask stale frame metadata in status even if worker snapshot is delayed.
24. Check failed native phase before and after selecting a PNG for HTTP delivery.

## Browser lifecycle and Stop
25. Keep owner Stop clickable while a slow status request is pending.
26. Disable re-launch of a registered/starting/stopping session.
27. Prevent a late approval-prepare HTTP response from opening the confirmation prompt after Stop.
28. Prevent a late execute success from replacing an already confirmed Stop status.
29. Do not re-enable Connect after a terminal task or pending Stop.
30. Revoke RDP pixels immediately when the browser tab becomes hidden.
31. Prevent new frame or status requests in hidden tabs, and discard an in-flight hidden response.
32. Remove visibility event listeners on close, Stop or detached panel.

## Internal native-entry guardrails
33. Reject a new local RDP cognitive worker after failure, finish or Stop.
34. Refuse attaching a new pinned RDP handshake after a terminal phase.
35. Refuse restarting native graphics after failure, finish or Stop.

## Evidence and constraints
- Native source, all new regression source files and Web JS were read back from GitHub.
- JavaScript syntax parsed successfully in the available V8 environment.
- No raw NUL bytes were found in the touched source files.
- Added test fixtures are still **unexecuted**. Manual Windows CI and full Python pytest were NOT run.
- No verified Windows RDP login, real framebuffer interoperability or latency metrics are claimed.
- Keyboard and mouse output remain unavailable; no automatic reconnect after password delegation.
- This draft must not be merged to main before the chained PRs and Windows CI receive independent review.
- Does not alter Android or Gusli endpoints, ports, credentials or source code.
