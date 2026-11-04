# Mandatory IP blocking check workflow

Trigger: whenever a user asks whether a particular IP address is blocked, filtered, or reachable from Russia.

Use **these specified instruments**, not model intuition or fabricated commands:
1. **Telnet / TCP Connect**: test TCP ports **22 and 3389** against the exact supplied IP, recording actual success, refusal, timeout, vantage point and timestamp. A closed port is not evidence of government blocking.
2. **Cheburcheck (Чебурчек)**: submit the same IP through its actual available integration, wait up to **120 seconds** for results, record the raw tool outcome or mark unavailable. Do not claim it was queried without a real response.
3. **Check-Host**: perform network checks from **Russian and non-Russian** nodes, capture node-level results and timestamps, and compare them.

Only after all three checks have either produced evidence or explicitly failed/unavailable, synthesize the result. Supplementary RIPE Atlas/OONI/traceroute may help but must not replace the three requested checks.

The agent MUST NOT label an IP as 'blocked by TSPU/RKN' based solely on TCP timeout, a single node, or Check-Host alone. Distinguish 'observed unreachable from tested network' from 'cause confirmed'. If any service lacks an implemented connector, say 'not checked: tool unavailable' and do not invent results. Do not report '100% confidence' without independently validated attribution.

Response template: target; tools actually run (with verifiable execution receipts); Russia vs abroad observations; alternative explanations; verdict (reachable / unreachable at tested vantage points / inconclusive); attribution (confirmed only with mechanism-specific evidence, otherwise unconfirmed); next action. No customer-facing categorical blocking claim without evidence.

Execution must respect site terms and authorized targets; avoid broad port scanning. Do not silently substitute general web search for the three instruments.
