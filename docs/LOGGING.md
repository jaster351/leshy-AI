# Centralized logging

Leshy 1.10.3 uses one canonical logging root:

`LeshyBrain/logs/`

No component may create log files under `runtime/`, `models/`, temporary directories, the application install directory, or arbitrary working directories.

All component-specific logs are direct children of the canonical root. Current expected files include:

- `leshy.log` — application/runtime events
- `problems.log` — warnings and errors
- `diagnostics.json` — current diagnostic snapshot
- `vulkan-server.log` — GPU llama.cpp backend
- `openvino-server.log` — NPU/OpenVINO llama.cpp backend
- `cpu-server.log` — CPU fallback llama.cpp backend

Future Web UI, Telegram/API, research/self-learning, updater and installer diagnostics must use the same root. Separate files are allowed, separate directories are not.

The shared helper in `src/leshy/logging_paths.py` is the only supported way to allocate new log-file paths. It rejects absolute and nested paths so a component cannot silently scatter logs elsewhere.

Logs containing secrets must continue to use the existing redaction rules. Rotation/retention policies may differ by file, but storage location must not.
