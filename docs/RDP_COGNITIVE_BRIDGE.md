# RDP visual reasoning via existing Leshy Planner LLM (read-only)

This branch is stacked on PR #357 (bounded local vision worker), itself
stacked on PR #356 (local VLM) and PR #353 (PNG graphics bridge). Do not
merge this branch directly to main until the prerequisite branches land.

The in-process RDPObservationInbox accepts only typed, read-only
RDPVisualObservation objects. A trusted owner-scoped scheduler calls
reason_once(existing_planner, owner_task=..., reauthorize=...).
The bridge uses **the same planner.llm** already selected by Leshy,
not a new hardcoded model or a scripted mock answer. It calls
planner.llm.chat directly, NOT Planner.run(), because Planner.run()
can dispatch tools. A separate read-only system message asks for one
structured suggestion. The screenshot and VLM text are passed as
untrusted tool data; the task comes separately from the real owner.

Possible responses: observe_more, ask_owner, propose_click, report.
The model is never allowed to invoke a tool, call an RDP method or
mint an action/owner approval. propose_click has validated x/y inside
the observed image but is ONLY a draft, with
execution_status=not_executed, owner_approval_issued=False and
input_enabled=False. This is not a durable ticket and contains no
single-use action receipt. A verified owner, fresh screenshot and
separate per-node approval gate are necessary before actual remote
input can be added.

The inbox holds only one latest observation and drops late LLM
responses after a newer frame, Stop or revoked permission. It keeps
no PNG images, passwords or logs. The blocking LLM call must run in
a bounded trusted task outside RDP TLS, VLM callbacks and HTTP handlers.
It can block for the configured LLM runtime timeout, but offers do not
block on model inference.

Still NOT implemented: wiring to the installed runtime scheduler/GUI,
an authenticated owner approval workflow for RDP clicks/keyboard,
native input protocol, Windows RDP E2E, resilient fast-path RDP,
integration tests, or a demonstrated complete autonomous workflow.
No tests or builds were run in this work item.
