# MIGRATION STATUS — Leshy 1.10.3

Источник миграции: CineMax / ветка Leshy 1.10.x — только чтение.
Рабочий репозиторий: `jaster351/leshy-AI`.
Рабочая ветка: `migration/cinemax-1.10.2-live`.

## Уже перенесено / зафиксировано

- версия 1.10.3;
- GPU-first интерактивный runtime с fallback;
- правила NPU cooldown после подтверждённых compute/memory ошибок;
- live-search и немедленное добавление candidate knowledge;
- счётчики knowledge total/candidate/trusted/stale/conflict;
- единый корень логов `LeshyBrain/logs`;
- обязательная ротация логов и ограничение дискового роста;
- UI-контракт вариант 4 → вариант 3;
- постоянный голосовой разговор в Web UI;
- полностью офлайн-голос: local STT → local LLM → local TTS;
- политика кроссплатформенных AI-ускорителей: Intel NPU, AMD Ryzen AI/XDNA, Apple Neural Engine, Qualcomm Hexagon и последующие совместимые ускорители;
- различение «ускоритель физически найден» и «runtime реально работоспособен»;
- обязательный health/smoke probe ускорителя перед использованием.

## Обязательные оставшиеся Gate

- [ ] Полный перенос Web UI варианта 4 → 3 в рабочее дерево.
- [ ] Полный перенос/адаптация Windows UI и установщика.
- [ ] Локальный TTS backend без облака.
- [ ] Offline voice smoke-test с заблокированным внешним интернетом.
- [ ] Barge-in: пользователь может перебить озвучивание.
- [ ] Автоматический возврат в режим прослушивания после ответа.
- [ ] Cross-platform hardware discovery: Windows x86-64/ARM64, Linux x86-64/ARM64, macOS Apple Silicon.
- [ ] Vendor-specific accelerator adapters/health probes для доступных Intel/AMD/Apple/Qualcomm платформ.
- [ ] Regression profiles: Intel CPU+GPU+NPU, AMD Ryzen AI, Apple Silicon, Qualcomm ARM64, CPU-only, accelerator-without-runtime, failed-health-probe.
- [ ] Полный перенос остальных исходников и тестов из read-only источника.
- [ ] Полный unit/regression test suite.
- [ ] CI на целевых ОС/архитектурах, где доступны runner/эмуляция.
- [ ] Windows frozen smoke-test.
- [ ] Финальная сборка установщика и GitHub Release.

## Правило готовности

1.10.3 не считается готовой, если современный AI-ускоритель обнаруживается системой, но Леший молча игнорирует его, либо если отказ ускорителя блокирует пользовательский чат. Релиз также не считается готовым без согласованного интерфейса, офлайн-голоса и прохождения релизных тестов.