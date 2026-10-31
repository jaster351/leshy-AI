# MIGRATION STATUS — Leshy 1.10.3

Источник миграции: `jaster351/CINEMAX-Marketplace-` — только чтение.
Рабочий репозиторий: `jaster351/leshy-AI`.
Рабочая ветка: `main`. Все дальнейшие изменения Leshy AI, тесты, документация и релизная сборка выполняются только здесь.

## Уже перенесено / зафиксировано

- версия 1.10.3;
- GPU-first интерактивный runtime с fallback;
- интерактивный LLM и embeddings: GPU → NPU → CPU;
- фоновые LLM-задачи: NPU → CPU → GPU;
- правила NPU cooldown/изоляции после подтверждённых compute/memory ошибок, включая `OUT_OF_HOST_MEMORY`, без повторного торможения интерактивных ответов до перезапуска или успешной повторной проверки;
- детерминированный live-search для актуальных запросов, явного веб-поиска и пробелов локальной БЗ;
- быстрый live-search немедленно добавляет найденное знание как `candidate`, глубокая проверка выполняется отдельно в фоне;
- раздельные счётчики `knowledge_total`, `candidate`, `trusted` (дополнительно stale/conflict);
- единый корень логов `LeshyBrain/logs`;
- репозиторная папка `log/` для пользовательских диагностических логов и их последующего анализа;
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
- [ ] Regression/unit тест на каждый найденный баг.
- [ ] Полный unit/regression test suite.
- [ ] CI на целевых ОС/архитектурах, где доступны runner/эмуляция.
- [ ] Windows frozen smoke-test.
- [ ] Проверенная сборка `LeshySetup-1.10.3.exe`.
- [ ] GitHub Release в `jaster351/leshy-AI` с проверенным установщиком.

## Правило готовности

1.10.3 не считается готовой, если современный AI-ускоритель обнаруживается системой, но Леший молча игнорирует его, либо если отказ ускорителя блокирует пользовательский чат. Версия также не считается готовой без согласованного Web/Windows UI, полностью офлайн-голоса, полного набора unit/regression тестов, Windows frozen smoke-test, проверенного `LeshySetup-1.10.3.exe` и опубликованного GitHub Release.

GPL-2.0 `LICENSE` сохраняется без замены лицензии.