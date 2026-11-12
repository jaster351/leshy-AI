# Mandatory IP blocking check workflow

Trigger: an explicit request to check whether a public IP is blocked or reachable from Russia.

## Three independent checks, launched in parallel

1. **Direct TCP Connect** from Leshy's machine: TCP/22 (SSH, commonly Linux/Unix) and TCP/3389 (RDP, commonly Windows). Record connected, refused, timeout or error separately. Refused is a reachable TCP response, NOT a TSPU timeout. A reachable port suggests a service, not proof of the operating system.
2. **Check-Host**: launch TCP checks separately for ports 22 and 3389, from selected Russian and foreign nodes. Save the request IDs and permanent report URLs. Poll the official `check-result/{request_id}` until every selected node returns a final outcome for both ports or a clearly reported maximum wait expires. `null` means *still in progress*, NEVER timeout. The application must not present a full Check-Host result while any node is pending.
3. **Cheburcheck**: call `/api/v1/check?target=IP` to get the signed `id` and static registry status; then stream `/api/v1/probe/{id}` (the argument is a query ID, not the IP). Read the real SSE `started`, `result` and `done` events, allowing up to the specified probe timeout. Only `done` signifies completed active scanning. A static listing is **not** the dynamic TSPU verdict.

All tools must be real and use the exact requested IP. Never fabricate measurements or treat service-unavailable as clear. Record timestamps, per-port outcomes, per-node country and status, source reports and verified tool receipts.

## Operator's 3-of-3 rule

Label **"Блокировка ТСПУ подтверждена по 3/3 диагностическим признакам"** only if ALL these are true:

- The direct TCP Connect **times out** on port 22 or 3389.
- **All selected Check-Host nodes finished** for both ports and at least one Russian node returned an actual **timeout on the SAME port** as the direct timeout.
- Cheburcheck active SSE scanning **finished with `done`** and at least one actual probe reports `tspu_block`.

The confirmation is a diagnosis by three matching independent observations, not packet-level proof of action by a particular TSPU device or a formally calibrated statistical probability. A response from a reachable service on one port cannot be used to prove the operating system. A 2-of-3 pattern is a likely blocking scenario, **not** confirmed; a direct timeout alone represents at most a preliminary **20-point heuristic**, never a proof. If a service is incomplete, unavailable or produces contradictory evidence, disclose that limitation.

## Required concise answer

- IP, timestamp, status (3/3 confirmed / 2/3 likely / inconclusive).
- TCP/22 and TCP/3389 outcomes **separately**, including which port matched the 3/3 rule.
- Check-Host successful and timeout counts for Russian locations and foreign locations for each port, plus pending count and permanent reports when available.
- Cheburcheck final probe status, number of responding probes, and raw verdict code(s).
- Tool receipts / source references retained in metadata for verification.
- Never label a TSPU block from static lists, a regional timeout, incomplete Check-Host results or an interrupted Cheburcheck SSE stream alone.

Respect target authorization and rate limits; do not scan unrelated ports or broad address ranges.
