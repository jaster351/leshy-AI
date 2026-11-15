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


## Безопасность публикации логов в GitHub (13.11.2026)

Автоматическая публикация runtime- и build-логов в ветку `logs/windows-local`
**заблокирована до появления проверенного fail-closed обезличивания**.
Локальные файлы `LeshyBrain/logs/` продолжают записываться для диагностики.
Оба канала загрузки блокируются: `DiagnosticPublisher.publish_once()`
и устаревший `windows/publish-build-logs.ps1`.

Предыдущий фильтр пропускал полные URL (включая query), пути Windows,
имена устройств и числовые Telegram chat_id; метка `sanitized: true`
не гарантировала безопасность. Запрещено публиковать как исходные файлы,
так и «очищенные» файлы до обязательной проверки полученного
выходного текста и негативных сценариев. Поля, не относящиеся к техническому
событию, нужно отбрасывать, а не пытаться угадывать их секретность.

Уже запушенные логи могли остаться в истории Git, других refs, клонах,
кэшах и архивных артефактах. Удаление текущей директории не очищает
предыдущие версии: требуется отдельная ревизия Git refs, переписывание
ветки логов и удаление кэшированных объектов на стороне GitHub.
Не включайте публикацию до окончания этой работы.
