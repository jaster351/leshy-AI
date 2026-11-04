# IP blocking diagnosis: evidence-first contract

This is a safety-critical support workflow. The agent MUST NOT infer a government/TSPU block from a failed connection alone.

## Required structured outcome

- target IP, protocol, port, timestamp and vantage points;
- each executed probe's actual tool receipt and sanitized result;
- DNS check only if a hostname was supplied;
- TCP reachability, optional traceroute and independent network vantage points;
- explicit separation of observation, hypothesis, and confirmed cause;
- status: reachable / unreachable-from-tested-vantage / inconclusive;
- blocking attribution: **unconfirmed** unless supported by independent, reproducible, mechanism-specific evidence;
- operator-facing response with limitations and next verification step.

No probe performed => no claim of a probe. No result receipt => no claim of tool execution. A receipt proves execution only, not correctness of the remote data. Avoid numerical confidence percentages without calibrated evidence. Never label TSPU/RKN blocking as 100% based solely on port failures or traceroute. Distinguish firewall, service-down, routing, ISP filtering and endpoint policy possibilities.

Any active probes must respect rate limits and target authorization; do not scan unrelated ports or broad address ranges. The diagnostic should fail closed when evidence is missing, and should not automatically send an unverified conclusion to a customer.
