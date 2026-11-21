# Native RDP vision worker — bounded in-process frame pipeline

This PR stacks on the local vision adapter PR #356, which itself depends on
RDP PNG bridge PR #353. The worker links the two existing components without
exposing any HTTP/Telegram/LLM routes or enabling remote keyboard and mouse.

## Runtime contract

- A trusted, already owner-authorized RDP coordinator starts exactly one
  RDPVisionWorker before entering the existing graphics receive loop.
- Its callback submit(frame) performs only bounded in-memory metadata
  validation and a constant-size queue replacement under a lock. There are
  no model/network calls in the TLS callback.
- The separate daemon worker executes the preexisting local VLM inference,
  enforcing owner reauthorization and rejecting forged or stale observations.
- One frame can be actively processed while only the latest subsequent
  frame remains queued. Replaced images and superseded model outputs are
  discarded; there is no accumulation of old screenshots.
- Stop clears the pending image and suppresses publication after stop
  completes. A running 45-second local model request cannot be forcibly
  interrupted, but it cannot publish a late result.
- An enqueue refusal propagates as a stream failure rather than pretending that an image was delivered. Inference failure raises a safe error without automatic retry.
- The explicit synchronous coordinator
  stream_native_rdp_with_local_vision() connects the pieces and optionally
  drains the worker after the graphics socket closes. It MUST NOT execute
  in a web handler or the TLS callback; every drain has a finite timeout.
- The sink is an explicitly trusted, nonblocking internal callback.
  The worker holds a brief cancellation lock around delivery, so a slow
  sink may delay Stop. Do not use remote requests as a sink.
- Status exposes only counters and exception *types*; never model text,
  prompts, screenshot contents, credentials or URLs.

No native input is implemented. Model output is **untrusted**.
It cannot approve SSH/RDP capabilities or create action receipts.
No automatic reconnection, retries or writes to disk are implemented.

No real Windows RDP E2E or test run has been performed for this draft.
Merge PR #353 then PR #356 then this stacked PR after independent review.
