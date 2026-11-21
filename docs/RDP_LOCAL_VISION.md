# Local vision inference for RDP — stacked on PR #353

The internal function describe_native_rdp_png takes a trusted RDPVisionPNG from
the existing read-only PR #353 stream and returns an RDPVisualObservation.
It reuses the existing Telegram vision backend discovery for locally installed
Ollama and loopback OpenAI-compatible multimodal models.

Security properties:
- Caller provides live owner/node/session/RDP authorization; checked before
  discovery, immediately before image transfer, and after inference.
- Only explicit IP loopback (127.0.0.1 or ::1), strict API route, no URL
  secrets, query, proxies or HTTP redirects. No external provider upload.
- RGB PNG checked for bounds; reject images over 8 MiB; no silent resize.
- Model output is untrusted descriptive data, NEVER a tool action, approved
  command, capability or frame receipt.
- No images, prompts or raw model results are written to persistent logs.
- No Web/Telegram endpoint is added and remote mouse/keyboard stay disabled.

The inference call is blocking (45-second bound) and must be used in a
bounded trusted worker, NOT inside the synchronous native RDP TLS read callback.
Connecting this result to the LLM planning loop remains separate, as do
owner-approved native input, real Windows RDP E2E and backend readiness.

Base: feat/rdp-native-vision-png-bridge-20261121 (PR #353).
Merge/rebase in dependency order; do not merge alone into main.
