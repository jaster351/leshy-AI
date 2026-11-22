# SuperAgent RDP — 30 coordinated lifecycle, approval and Windows-test tasks

Date: 2026-11-22. This is a single stacked change set above draft PR #373.
The list records concrete code changes or added regression cases. A checked
code change is **not** proof that it passed a real Windows RDP E2E run.

## Durable ticket lifecycle (8)

1. Hold an original explicitly requested native-RDP ticket in SQLite
   \`working\` while waiting for an independent human launch approval.
2. Keep generic SuperAgent LLM summaries from prematurely completing this
   specific owner-authorized remote-desktop ticket.
3. Require \`remote_authorized_at_creation is True\` before allowing that wait.
4. Reject the wait when either \`superagent\` or \`remote-admin\` is disabled.
5. Record "waiting for owner confirmation, no connection" as a true state
   report, never label it a connected RDP desktop.
6. Block startup recovery of interrupted RDP tickets, rather than
   automatically scheduling another credential/login attempt.
7. Deny explicit \`retry\` of an old RDP ticket: new confirmation requires
   a new owner-verified task.
8. Deny generic transient retry for an RDP task and present the current
   waiting report in ticket formatting.

## One-use credential authorization and Stop (10)

9. Revoke pending RDP tokens when the owner clicks Stop, even if no channel
   has been registered yet.
10. Invalidate previously consumed, still-live credential leases on Stop.
11. Revoke original ticket tokens before updating its durable Stop state.
12. Invalidate all outstanding RDP approvals and consent leases on service
    shutdown, including a later start in the same process.
13. Bind token issuance and delegation callbacks to per-owner/per-ticket
    Stop generations, not simply to a browser cookie.
14. Fail closed by globally invalidating old leases when Stop generation
    registry capacity is exhausted.
15. Rotate/burn the older unconsumed token when the same owner browser
    prepares that ticket again.
16. Limit pending confirmations for any one owner to eight.
17. Retain Stop-generation records while newly issued valid consent
    leases remain within their independent short expiry.
18. Add dedicated regression scenarios covering Stop before launch,
    Stop after token consumption, owner-scope isolation, global shutdown,
    consent rotation, limits, generation expiry and concurrency.

## Connection shutdown, target scope and UI (10)

19. Distinguish "Stop in progress" from "no session" in authenticated
    owner-scoped RDP status.
20. Distinguish failed pinned-TLS channel closure from successful Stop and
    keep that ticket blocked against another connection.
21. Inform the owner of these states without presenting PNG pixels or
    enabling remote keyboard/mouse input during uncertain closure.
22. Refuse a new RDP target when the original owner task includes a
    different literal IPv4 address.
23. Reject ambiguous original mandates listing multiple distinct IPv4
    addresses instead of guessing one.
24. Reject malformed explicitly provided literal IPv4 addresses.
25. Refuse mismatched bracketed IPv6 target literals in the original task.
26. Check original task mandate \`scope\` still matches the request text.
27. Check mandate \`owner_id\` matches the verified actor if recorded.
28. Check mandate creation timestamp matches original ticket if recorded.

## Windows and real state checks (2)

29. Add a separate opt-in \`windows-latest\` workflow_dispatch job with the
    actual SuperAgent/RDP owner, cognitive, PNG, approval and credentials
    regression modules. Keep default dispatch manual and opt-in off.
30. Add in-memory **actual SQLite** transition and crash-recovery tests
    for waiting RDP tickets, alongside the mock and negative tests.

## Actual verification in this turn

- Source files were read back from the private GitHub connector; checked
  RDP/Python sources contained zero raw NUL bytes.
- The updated Web JavaScript parsed successfully with V8.
- Existing base PRs #353/#356/#357/#358/#359/#367/#369/#370/#371/
  #372/#373 remain separate dependencies; none are merged here.
- No GitHub Actions workflow, Python pytest or external Windows RDP
  logon/bitmap/Stop E2E was executed in this turn. This PR stays DRAFT.
- No Guacamole, guacd, PostgreSQL RDP gateway, fake screenshot, automatic
  password retry or AI-authored owner grant has been introduced.

Deployment acceptance requires independent code review, explicit workflow
dispatch, test Windows host owned by the operator, evidence of pinned TLS/NLA
and actually decoded framebuffer, then a staged merge of dependency PRs.
